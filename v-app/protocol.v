module main

import json2

fn encode_json[T](value T) string {
	return json2.encode(value, escape_unicode: true, time_as_unix: true)
}

const history_limit = 100
const max_users = 2000
const max_frame = 16384
const max_sessions = 4
const max_text_bytes = 4096
const max_nonce_bytes = 64

struct User {
	id       int
	username string
mut:
	online bool
}

struct ChatMessage {
	id      u64
	from    int
	to      int
	text    string
	sent_at i64
	nonce   string
}

struct UsersEvent {
	kind  string = 'users' @[json: 'type']
	users []User
}

struct ReadyEvent {
	kind string = 'ready' @[json: 'type']
	user User
}

struct MessageEvent {
	kind    string = 'message' @[json: 'type']
	message ChatMessage
}

struct ErrorEvent {
	kind  string = 'error' @[json: 'type']
	error string
}

struct Registration {
	username string
}

struct Registered {
	user  User
	token string
}

struct UsersResponse {
	users []User
}

struct MessagesResponse {
	messages []ChatMessage
}

struct Incoming {
	kind  string @[json: 'type']
	token string
	to    int
	text  string
	nonce string
}

// The frame stays alive during this synchronous call. The decoder retains only
// token offsets; decoded Incoming strings own their bytes, including on reuse.
fn decode_incoming_frame(payload []u8, mut buffer json2.DecodeBuffer) !Incoming {
	return json2.decode_reuse[Incoming](unsafe { reuse_data_as_string(payload) }, mut buffer)
}
