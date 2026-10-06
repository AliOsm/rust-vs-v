module main

#include "@VMODROOT/shutdown.h"

fn C.chat_install_signals() int
fn C.chat_shutdown_requested() int
fn C.chat_request_shutdown()

fn install_shutdown_signals() ! {
	if C.chat_install_signals() != 0 { return error('Cannot install shutdown handlers') }
}

fn shutdown_requested() bool {
	return C.chat_shutdown_requested() != 0
}

fn request_shutdown() {
	C.chat_request_shutdown()
}
