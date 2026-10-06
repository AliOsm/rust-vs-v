"""Run both apps and forward shutdown to their processes."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
children = []


def stop(*_):
    for child in children:
        if child.poll() is None:
            child.terminate()


try:
    # Fail before starting either app if the default ports are occupied.
    for host, port in ((os.getenv("HOST", "127.0.0.1"), 3001), ("0.0.0.0", 3002)):
        with socket.socket() as probe:
            probe.bind((host, port))
    for port, binary in ((3001, ".build/rust/release/rust-chat"), (3002, ".build/v/v-chat")):
        children.append(subprocess.Popen([str(ROOT / binary)], cwd=ROOT,
            env={**os.environ, "PORT": str(port)}))
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    print("Rust: http://localhost:3001\nV:    http://localhost:3002\nCtrl+C stops both.", flush=True)
    while all(child.poll() is None for child in children):
        time.sleep(.25)
finally:
    stop()
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
