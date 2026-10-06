module main

import sync.stdatomic

fn main() {
	mut app := &App{}
	run_app(mut app) or {
		eprintln(err)
		exit(1)
	}
	// run_app has joined every worker before its error flag is inspected.
	if stdatomic.load_u64(&app.failed) != 0 {
		eprintln('Server worker failed')
		exit(1)
	}
}
