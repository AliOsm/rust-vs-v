#ifndef CHAT_SHUTDOWN_H
#define CHAT_SHUTDOWN_H
#include <signal.h>
#include <stdatomic.h>

/* Signals may run on any HTTP/reactor thread. A guaranteed lock-free C11
 * atomic is safe both across threads and inside the signal handler. */
#if ATOMIC_INT_LOCK_FREE != 2
#error "Chat shutdown requires lock-free atomic int"
#endif
static _Atomic int chat_stop_requested = 0;
static void chat_signal_handler(int signo) {
    (void)signo;
    atomic_store(&chat_stop_requested, 1);
}
static int chat_install_signals(void) {
    struct sigaction action = {0};
    action.sa_handler = chat_signal_handler;
    sigemptyset(&action.sa_mask);
    if (sigaction(SIGINT, &action, NULL) != 0) return -1;
    return sigaction(SIGTERM, &action, NULL);
}
static int chat_shutdown_requested(void) {
    return atomic_load(&chat_stop_requested);
}
static void chat_request_shutdown(void) {
    atomic_store(&chat_stop_requested, 1);
}
#endif
