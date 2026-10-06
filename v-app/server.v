module main

import net.websocket
import os
import runtime
import sync.stdatomic
import time
import veb

const reactor_count = 2
const outbox_capacity = 64

pub struct Context {
	veb.Context
}

pub struct App {
mut:
	store            shared Store
	reactors         []&websocket.Reactor
	next_reactor     u64
	stopping         u64
	failed           u64
	shutdown_threads []thread
}

pub fn (mut app App) init_server(server &veb.Server) {
	app.shutdown_threads << spawn watch_shutdown(server, mut app)
}

fn watch_shutdown(server &veb.Server, mut app App) {
	for stdatomic.load_u64(&app.stopping) == 0 {
		if shutdown_requested() {
			stdatomic.store_u64(&app.stopping, 1)
			server.shutdown(timeout: 3 * time.second) or {
				eprintln('HTTP shutdown failed: ${err}')
				stdatomic.store_u64(&app.failed, 1)
			}
			return
		}
		time.sleep(20 * time.millisecond)
	}
}

fn run_chat_reactor(mut reactor websocket.Reactor, mut app App) {
	reactor.run() or {
		eprintln('WebSocket reactor failed: ${err}')
		stdatomic.store_u64(&app.failed, 1)
		request_shutdown()
	}
}

fn run_app(mut app App) ! {
	install_shutdown_signals()!

	port := os.getenv_opt('PORT') or { '3002' }.int()
	// The pinned fasthttp backend uses all interfaces and automatic HTTP workers.
	host := '0.0.0.0'
	workers := runtime.nr_cpus()
	mut reactor_threads := []thread{}
	defer {
		stdatomic.store_u64(&app.stopping, 1)
		for mut reactor in app.reactors {
			reactor.stop()
		}
		reactor_threads.wait()
		app.shutdown_threads.wait()
	}
	for _ in 0 .. reactor_count {
		mut reactor := websocket.new_reactor(
			max_message_bytes:    max_frame
			max_pending_messages: outbox_capacity
			on_open:              attached
			on_message:           incoming
			on_close:             disconnected
			user:                 &ChatWorker{
				app: app
			}
		)!
		app.reactors << reactor
		reactor_threads << spawn run_chat_reactor(mut reactor, mut app)
	}
	println('V / veb (fasthttp) listening on http://${host}:${port} (${workers} HTTP workers, ${reactor_count} WebSocket reactors)')
	veb.run_at[App, Context](mut app,
		host:                    host
		port:                    port
		family:                  .ip
		max_request_buffer_size: max_frame + 4096
		show_startup_message:    false
	)!
}
