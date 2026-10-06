module main

import json2
import net.websocket
import sync.stdatomic
import time

const idle_timeout = 60 * time.second

struct Peer {
	client &websocket.ReactorClient
}

// Only the owning reactor's callbacks access this connection state.
struct ChatConnection {
	peer &Peer
mut:
	user_id int
}

// Each reactor has its own callback context; shared routing stays in Store.
struct ChatWorker {
	app &App
mut:
	json_input  json2.DecodeBuffer
	connections map[string]&ChatConnection
	json_output []u8
}

fn (peer &Peer) send(text string) {
	mut client := peer.client
	client.write_string(text) or {
		client.close(1013, 'Output queue full') or {}
		return
	}
}

fn (peer &Peer) send_bytes(payload []u8) {
	mut client := peer.client
	// The owning reactor copies inline; another reactor copies into its mailbox.
	client.write(payload, .text_frame) or {
		client.close(1013, 'Output queue full') or {}
		return
	}
}

fn (app &App) broadcast_directory() {
	if stdatomic.load_u64(&app.stopping) != 0 { return }
	mut peers := []&Peer{}
	mut users := []User{}
	rlock app.store {
		users = app.store.directory()
		for id, _ in app.store.authenticated {
			if peer := app.store.connections[id] {
				peers << peer
			}
		}
	}
	event := encode_json(UsersEvent{ users: users })
	for peer in peers {
		peer.send(event)
	}
}

fn attached(mut client websocket.ReactorClient, ref voidptr) {
	mut worker := unsafe { &ChatWorker(ref) }
	mut app := worker.app
	peer := &Peer{
		client: unsafe { &client }
	}
	worker.connections[client.id] = &ChatConnection{
		peer: peer
	}
	lock app.store {
		app.store.connections[client.id] = peer
	}
}

fn disconnected(mut client websocket.ReactorClient, _code int, _reason string, ref voidptr) {
	mut worker := unsafe { &ChatWorker(ref) }
	mut app := worker.app
	worker.connections.delete(client.id)
	mut was_authenticated := false
	lock app.store {
		app.store.connections.delete(client.id)
		if uid := app.store.authenticated[client.id] {
			was_authenticated = true
			app.store.authenticated.delete(client.id)
			app.store.sessions[uid] = app.store.sessions[uid].filter(it != client.id)
			if app.store.sessions[uid].len == 0 {
				app.store.sessions.delete(uid)
			}
		}
	}
	if was_authenticated {
		app.broadcast_directory()
	}
}

fn incoming(mut client websocket.ReactorClient, frame &websocket.Message, ref voidptr) {
	mut worker := unsafe { &ChatWorker(ref) }
	mut connection := worker.connections[client.id] or { return }
	peer := connection.peer
	if frame.opcode == .pong {
		return
	}
	if frame.opcode != .text_frame {
		peer.send_error('Text frames only')
		return
	}
	if frame.payload.len > max_frame {
		client.close(1009, 'Frame too large') or {}
		return
	}
	input := decode_incoming_frame(frame.payload, mut worker.json_input) or {
		peer.send_error('Invalid JSON')
		return
	}
	id := connection.user_id
	if id == 0 {
		worker.authenticate(mut client, mut connection, input)
		return
	}
	if input.kind == 'ping' {
		peer.send('{"type":"pong"}')
		return
	}
	if input.kind != 'send' {
		peer.send_error('Unknown message type')
		return
	}
	if input.text.contains_only(' \t\r\n') || input.text.len > max_text_bytes
		|| input.nonce.len > max_nonce_bytes {
		peer.send_error('Message must contain 1–4096 UTF-8 bytes; nonce at most 64 bytes')
		return
	}
	worker.deliver(id, input, peer)
}

fn (worker &ChatWorker) authenticate(mut client websocket.ReactorClient, mut connection ChatConnection, input Incoming) {
	mut app := worker.app
	peer := connection.peer
	mut user := User{}
	mut accepted := false
	lock app.store {
		uid := app.store.tokens[input.token] or { 0 }
		if input.kind == 'auth' && uid != 0 && app.store.sessions[uid].len < max_sessions {
			app.store.authenticated[client.id] = uid
			connection.user_id = uid
			app.store.sessions[uid] << client.id
			user = app.store.users[uid]
			user.online = true
			accepted = true
		}
	}
	if !accepted {
		client.write_string(encode_json(ErrorEvent{ error: 'Unauthorized' })) or {}
		client.close(1008, 'Unauthorized') or {}
		return
	}
	client.set_read_timeout(idle_timeout) or {}
	peer.send(encode_json(ReadyEvent{ user: user }))
	app.broadcast_directory()
}

fn (mut worker ChatWorker) deliver(id int, input Incoming, peer &Peer) {
	mut app := worker.app
	// Both users can have at most max_sessions connections. Copy the handles
	// under the store lock, then send after releasing it.
	mut recipients := [max_sessions * 2]Peer{init: Peer{
		client: peer.client
	}}
	mut recipient_count := 0
	mut message := ChatMessage{}
	mut valid := false
	lock app.store {
		valid = input.to != id && input.to in app.store.users
		if valid {
			app.store.sequence++
			message = ChatMessage{
				id:      app.store.sequence
				from:    id
				to:      input.to
				text:    input.text
				sent_at: time.now().unix_milli()
				nonce:   input.nonce
			}
			key := conversation_key(id, input.to)
			mut history := app.store.history[key] or { History{} }
			history.add(message)
			app.store.history[key] = history
			for uid in [id, input.to]! {
				for connection_id in app.store.sessions[uid] {
					if recipient := app.store.connections[connection_id] {
						recipients[recipient_count] = *recipient
						recipient_count++
					}
				}
			}
		}
	}
	if !valid {
		peer.send_error('Choose another registered user')
		return
	}
	worker.json_output.clear()
	json2.encode_append(MessageEvent{ message: message }, mut worker.json_output,
		escape_unicode: true
		time_as_unix:   true
	)
	for i in 0 .. recipient_count {
		recipients[i].send_bytes(worker.json_output)
	}
}

fn (peer &Peer) send_error(message string) {
	peer.send(encode_json(ErrorEvent{ error: message }))
}
