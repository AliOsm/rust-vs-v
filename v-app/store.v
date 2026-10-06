module main

struct History {
mut:
	messages []ChatMessage
	next     int
}

struct Store {
mut:
	users         map[int]User
	names         map[string]int
	tokens        map[string]int
	connections   map[string]&Peer
	authenticated map[string]int
	sessions      map[int][]string
	history       map[u64]History
	sequence      u64
}

fn conversation_key(a int, b int) u64 {
	// User IDs are positive ints; two ordered 32-bit halves avoid string allocation.
	low, high := if a < b { a, b } else { b, a }
	return (u64(u32(low)) << 32) | u64(u32(high))
}

fn (store &Store) directory() []User {
	mut users := []User{cap: store.users.len}
	for _, user in store.users {
		mut u := user
		u.online = store.sessions[u.id].len > 0
		users << u
	}
	users.sort(a.id < b.id)
	return users
}

// The cursor points to the oldest message once the ring is full.
fn (mut history History) add(message ChatMessage) {
	if history.messages.len < history_limit {
		history.messages << message
	} else {
		history.messages[history.next] = message
		history.next = (history.next + 1) % history_limit
	}
}

fn (history &History) ordered() []ChatMessage {
	if history.messages.len < history_limit {
		return history.messages.clone()
	}
	mut messages := []ChatMessage{cap: history_limit}
	messages << history.messages[history.next..]
	messages << history.messages[..history.next]
	return messages
}
