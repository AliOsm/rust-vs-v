//! In-memory accounts, bounded history, and nonblocking connection mailboxes.
use crate::protocol::{ChatMessage, User};
use actix_web::web;
use serde_json::json;
use std::{
    collections::{HashMap, VecDeque},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
};
use tokio::sync::mpsc;

pub(crate) const HISTORY_LIMIT: usize = 100;
pub(crate) const MAX_USERS: usize = 2000;
pub(crate) type State = web::Data<Mutex<Store>>;

#[derive(Clone)]
pub(crate) struct Peer {
    pub(crate) tx: mpsc::Sender<String>,
    pub(crate) overloaded: Arc<AtomicBool>,
}

impl Peer {
    pub(crate) fn send_error(&self, message: &str) {
        self.send(&json!({"type": "error", "error": message}).to_string());
    }

    pub(crate) fn send(&self, text: &str) {
        if self.tx.try_send(text.to_owned()).is_err() {
            self.overloaded.store(true, Ordering::Relaxed);
        }
    }
}

#[derive(Default)]
pub(crate) struct Store {
    pub(crate) users: HashMap<u64, User>,
    pub(crate) names: HashMap<String, u64>,
    pub(crate) tokens: HashMap<String, u64>,
    pub(crate) peers: HashMap<u64, HashMap<u64, Peer>>,
    pub(crate) history: HashMap<(u64, u64), VecDeque<ChatMessage>>,
    pub(crate) sequence: u64,
}

impl Store {
    pub(crate) fn directory(&self) -> Vec<User> {
        let mut users: Vec<_> = self.users.values().cloned().collect();
        for user in &mut users {
            user.online = self.peers.get(&user.id).is_some_and(|p| !p.is_empty());
        }
        users.sort_unstable_by_key(|u| u.id);
        users
    }
}

pub(crate) fn conversation_key(a: u64, b: u64) -> (u64, u64) {
    (a.min(b), a.max(b))
}

pub(crate) fn broadcast_directory(state: &State) {
    let (event, peers) = {
        let store = state.lock().unwrap();
        (
            json!({"type": "users", "users": store.directory()}).to_string(),
            store
                .peers
                .values()
                .flat_map(|p| p.values().cloned())
                .collect::<Vec<_>>(),
        )
    };
    for peer in peers {
        peer.send(&event);
    }
}
