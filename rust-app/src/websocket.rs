//! WebSocket authentication, private delivery, heartbeat, and cleanup.
use crate::{
    http::{error, same_origin},
    protocol::{
        ChatMessage, Incoming, MAX_FRAME_BYTES, MAX_NONCE_BYTES, MAX_SESSIONS, MAX_TEXT_BYTES,
    },
    state::{HISTORY_LIMIT, Peer, State, broadcast_directory, conversation_key},
};
use actix_web::{HttpRequest, HttpResponse, http::StatusCode, web};
use actix_ws::{AggregatedMessage, CloseCode, CloseReason};
use futures_util::StreamExt;
use serde_json::json;
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tokio::{
    sync::mpsc,
    time::{self, Instant},
};

const OUTBOX_CAPACITY: usize = 64;
const AUTH_TIMEOUT: Duration = Duration::from_secs(5);
const WRITE_TIMEOUT: Duration = Duration::from_secs(5);
const HEARTBEAT_INTERVAL: Duration = Duration::from_secs(5);
const IDLE_TIMEOUT: Duration = Duration::from_secs(60);
static NEXT_CONNECTION_ID: AtomicU64 = AtomicU64::new(1);

fn process_message(id: u64, text: &str, state: &State, own: &Peer) {
    let Ok(input) = serde_json::from_str::<Incoming>(text) else {
        own.send_error("Invalid JSON");
        return;
    };
    if input.kind == "ping" {
        own.send(r#"{"type":"pong"}"#);
        return;
    }
    if input.kind != "send" {
        own.send_error("Unknown message type");
        return;
    }
    if input.text.trim_matches([' ', '\t', '\r', '\n']).is_empty()
        || input.text.len() > MAX_TEXT_BYTES
        || input.nonce.len() > MAX_NONCE_BYTES
    {
        own.send_error("Message must contain 1–4096 UTF-8 bytes; nonce at most 64 bytes");
        return;
    }
    let (message, peers) = {
        let mut store = state.lock().unwrap();
        if input.to == id || !store.users.contains_key(&input.to) {
            own.send_error("Choose another registered user");
            return;
        }
        store.sequence += 1;
        let message = ChatMessage {
            id: store.sequence,
            from: id,
            to: input.to,
            text: input.text,
            sent_at: SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_millis() as u64,
            nonce: input.nonce,
        };
        let entries = store
            .history
            .entry(conversation_key(id, input.to))
            .or_default();
        if entries.len() == HISTORY_LIMIT {
            entries.pop_front();
        }
        entries.push_back(message.clone());
        let peers = [id, input.to]
            .iter()
            .filter_map(|uid| store.peers.get(uid))
            .flat_map(|p| p.values().cloned())
            .collect::<Vec<_>>();
        (message, peers)
    };
    let event = json!({"type": "message", "message": message}).to_string();
    for peer in peers {
        peer.send(&event);
    }
}

pub(crate) async fn upgrade(
    req: HttpRequest,
    stream: web::Payload,
    state: State,
) -> actix_web::Result<HttpResponse> {
    if !same_origin(&req) {
        return Ok(error(StatusCode::FORBIDDEN, "Origin not allowed"));
    }
    let (response, mut session, messages) = actix_ws::handle(&req, stream)?;
    let mut messages = messages
        .max_frame_size(MAX_FRAME_BYTES)
        .aggregate_continuations()
        .max_continuation_size(MAX_FRAME_BYTES);
    actix_web::rt::spawn(async move {
        let first = time::timeout(AUTH_TIMEOUT, messages.next()).await;
        let id = match first {
            Ok(Some(Ok(AggregatedMessage::Text(text)))) => serde_json::from_str::<Incoming>(&text)
                .ok()
                .filter(|m| m.kind == "auth")
                .and_then(|m| state.lock().unwrap().tokens.get(&m.token).copied()),
            _ => None,
        };
        let Some(id) = id else {
            let _ = session
                .text(r#"{"type":"error","error":"Unauthorized"}"#)
                .await;
            let _ = session
                .close(Some(CloseReason {
                    code: CloseCode::Policy,
                    description: None,
                }))
                .await;
            return;
        };
        let connection_id = NEXT_CONNECTION_ID.fetch_add(1, Ordering::Relaxed);
        let (tx, mut rx) = mpsc::channel::<String>(OUTBOX_CAPACITY);
        let peer = Peer {
            tx,
            overloaded: Arc::new(AtomicBool::new(false)),
        };
        let ready = {
            let mut store = state.lock().unwrap();
            let count = store.peers.get(&id).map_or(0, |p| p.len());
            if count >= MAX_SESSIONS {
                None
            } else {
                store
                    .peers
                    .entry(id)
                    .or_default()
                    .insert(connection_id, peer.clone());
                let mut user = store.users[&id].clone();
                user.online = true;
                Some(json!({"type": "ready", "user": user}).to_string())
            }
        };
        let Some(ready) = ready else {
            let _ = session
                .close(Some(CloseReason {
                    code: CloseCode::Policy,
                    description: Some("Session limit reached".into()),
                }))
                .await;
            return;
        };
        peer.send(&ready);
        broadcast_directory(&state);
        let mut last_seen = Instant::now();
        let mut heartbeat = time::interval(HEARTBEAT_INTERVAL);
        loop {
            if peer.overloaded.load(Ordering::Relaxed) {
                break;
            }
            tokio::select! {
                event = messages.next() => match event {
                    Some(Ok(AggregatedMessage::Text(text))) => {
                        last_seen = Instant::now();
                        process_message(id, &text, &state, &peer);
                    }
                    Some(Ok(AggregatedMessage::Ping(bytes))) => {
                        last_seen = Instant::now();
                        if session.pong(&bytes).await.is_err() {
                            break;
                        }
                    }
                    Some(Ok(AggregatedMessage::Pong(_))) => {
                        last_seen = Instant::now();
                    }
                    Some(Ok(AggregatedMessage::Binary(_))) => peer.send_error("Text frames only"),
                    _ => break,
                },
                Some(event) = rx.recv() => {
                    if !matches!(time::timeout(WRITE_TIMEOUT, session.text(event)).await, Ok(Ok(()))) {
                        break;
                    }
                }
                _ = heartbeat.tick() => {
                    if last_seen.elapsed() > IDLE_TIMEOUT {
                        break;
                    }
                },
            }
        }
        {
            let mut store = state.lock().unwrap();
            if let Some(peers) = store.peers.get_mut(&id) {
                peers.remove(&connection_id);
                if peers.is_empty() {
                    store.peers.remove(&id);
                }
            }
        }
        broadcast_directory(&state);
        let _ = session.close(None).await;
    });
    Ok(response)
}
