"""Process and protocol helpers shared by the tests and benchmark controller."""
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time

import aiohttp
import psutil

ROOT = Path(__file__).resolve().parents[1]
BINARIES = {
    "rust": ROOT / ".build/rust/release/rust-chat",
    "v": ROOT / ".build/v/v-chat",
}
DEFAULT_APPS = list(BINARIES)


class Server:
    def __init__(self, language, *, cpus=None, workers=2, trace_path=None,
                 trace_filter="recvfrom", launch_prefix=None):
        self.language, self.cpus, self.workers = language, cpus, workers
        self.trace_path = trace_path
        self.trace_filter = trace_filter
        self.launch_prefix = launch_prefix or []

    async def __aenter__(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        logdir = ROOT / ".build/test-logs"
        logdir.mkdir(parents=True, exist_ok=True)
        self.log_path = logdir / f"{self.language}-{self.port}.log"
        self.log = self.log_path.open("w")
        command = [str(BINARIES[self.language])]
        if self.trace_path is not None:
            # Traced diagnostics do not use this wrapper's CPU/RSS for benchmarks.
            command = ["strace", "-f", "-qq", "-c", "-e", f"trace={self.trace_filter}", "-o", str(self.trace_path), *command]
        command = [*self.launch_prefix, *command]
        if self.cpus:
            command = ["taskset", "-c", ",".join(map(str, self.cpus)), *command]
        started = time.perf_counter()
        # The V app uses all IPv4 interfaces and automatic HTTP workers on both
        # revisions. taskset still enforces the same CPU budget for each build.
        self.child = subprocess.Popen(command, cwd=ROOT, stdout=self.log, stderr=self.log,
            start_new_session=True,
            env={**os.environ, "HOST": "127.0.0.1", "PORT": str(self.port), "WORKERS": str(self.workers)})
        self.process = psutil.Process(self.child.pid)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1)) as http:
                while time.perf_counter() - started < 15:
                    if self.child.poll() is not None:
                        raise RuntimeError(self.log_path.read_text())
                    try:
                        async with http.get(self.url + "/health") as response:
                            if response.status == 200:
                                self.startup_ms = (time.perf_counter() - started) * 1000
                                return self
                    except (aiohttp.ClientError, TimeoutError):
                        pass
                    await asyncio.sleep(.002)
            raise TimeoutError(f"{self.language} did not start: {self.log_path.read_text()}")
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, *_):
        # Own process group also covers the app when launched under strace.
        try:
            os.killpg(self.child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if self.child.poll() is None:
            try:
                await asyncio.to_thread(self.child.wait, timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.child.pid, signal.SIGKILL)
                await asyncio.to_thread(self.child.wait)
        self.log.close()


async def register(http, url, username):
    async with http.post(url + "/api/register", json={"username": username}) as response:
        data = await response.json()
        assert response.status == 201, (response.status, data)
        return data


class ChatClient:
    def __init__(self, ws, account):
        self.ws, self.account = ws, account
        self.messages = asyncio.Queue()
        self.errors = asyncio.Queue()
        self.users = []
        self.ready = asyncio.get_running_loop().create_future()
        self.reader = asyncio.create_task(self.read())

    @classmethod
    async def connect(cls, http, url, account):
        ws = await http.ws_connect(url + "/ws", compress=0)
        client = cls(ws, account)
        await ws.send_json({"type": "auth", "token": account["token"]})
        await asyncio.wait_for(client.ready, 5)
        return client

    async def read(self):
        try:
            async for frame in self.ws:
                if frame.type != aiohttp.WSMsgType.TEXT:
                    continue
                event = json.loads(frame.data)
                if event["type"] == "ready" and not self.ready.done():
                    self.ready.set_result(event)
                elif event["type"] == "users":
                    self.users = event["users"]
                elif event["type"] == "message":
                    self.messages.put_nowait(event["message"])
                elif event["type"] == "error":
                    self.errors.put_nowait(event["error"])
        finally:
            if not self.ready.done():
                self.ready.set_exception(RuntimeError("Socket closed before ready"))

    async def send(self, other, text, nonce=""):
        await self.ws.send_json({"type": "send", "to": other, "text": text, "nonce": nonce})

    async def receive(self):
        return await asyncio.wait_for(self.messages.get(), 5)

    async def close(self):
        await self.ws.close()
        await self.reader


def cpu_seconds(process):
    cpu = process.cpu_times()
    return cpu.user + cpu.system
