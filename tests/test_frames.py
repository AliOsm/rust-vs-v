"""Exercise real TCP boundaries and WebSocket fragmentation against all app variants."""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import socket
import struct
from urllib.parse import urlsplit

import aiohttp
import uvloop

from common import BINARIES, DEFAULT_APPS, ROOT, ChatClient, Server, register


def frame(payload=b"", *, opcode=1, fin=True, rsv=0):
    """Encode a masked client frame independently of either server library."""
    size = len(payload)
    head = bytes([(0x80 if fin else 0) | rsv | opcode])
    if size < 126:
        head += bytes([0x80 | size])
    elif size <= 65535:
        head += bytes([0x80 | 126]) + struct.pack("!H", size)
    else:
        head += bytes([0x80 | 127]) + struct.pack("!Q", size)
    mask = os.urandom(4)
    return head + mask + bytes(value ^ mask[i % 4] for i, value in enumerate(payload))


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class RawWebSocket:
    @classmethod
    async def open(cls, url):
        target = urlsplit(url)
        reader, writer = await asyncio.open_connection(target.hostname, target.port)
        writer.get_extra_info("socket").setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (f"GET /ws HTTP/1.1\r\nHost: {target.netloc}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n")
        writer.write(request.encode())
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
        assert response.startswith(b"HTTP/1.1 101"), response
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        assert accept in response, response
        client = cls()
        client.reader, client.writer = reader, writer
        return client

    async def send(self, wire, *, split=False):
        if not split:
            self.writer.write(wire)
            await self.writer.drain()
            return
        # Separate writes and delays force the peer to handle incomplete fields.
        cuts = sorted({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 13, 17, len(wire) // 2, len(wire) - 1, len(wire)})
        cuts = [position for position in cuts if 0 <= position <= len(wire)]
        for start, end in zip(cuts, cuts[1:]):
            self.writer.write(wire[start:end])
            await self.writer.drain()
            await asyncio.sleep(.003)

    async def receive(self):
        first, second = await self.reader.readexactly(2)
        assert first & 0x80 and not first & 0x70, (first, second)
        assert not second & 0x80, "Server frames must be unmasked"
        length = second & 0x7f
        if length == 126:
            length = struct.unpack("!H", await self.reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
        assert length < 131072
        return first & 0xf, await self.reader.readexactly(length)

    async def event(self, kind):
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await self.receive()
                assert opcode == 1, (opcode, payload)
                event = json.loads(payload)
                if event["type"] == kind:
                    return event
                assert event["type"] == "users", event

    async def pong(self, expected):
        async with asyncio.timeout(5):
            while True:
                opcode, payload = await self.receive()
                if opcode == 10:
                    assert payload == expected
                    return
                assert opcode == 1 and json.loads(payload)["type"] == "users", (opcode, payload)

    async def close(self):
        self.writer.close()
        try:
            await self.writer.wait_closed()
        except ConnectionError:
            pass


async def check(app):
    checks = []
    async with Server(app) as server, aiohttp.ClientSession() as http:
        alice = await register(http, server.url, "frame_alice")
        bob = await register(http, server.url, "frame_bob")
        recipient = await ChatClient.connect(http, server.url, bob)
        sender = await RawWebSocket.open(server.url)
        try:
            await sender.send(frame(encode({"type": "auth", "token": alice["token"]})), split=True)
            assert (await sender.event("ready"))["user"]["id"] == alice["user"]["id"]
            checks.append("Authentication with split TCP header, masking key and payload")

            async def delivered(expected):
                echo = (await sender.event("message"))["message"]
                received = await recipient.receive()
                assert echo == received
                assert received["text"] == expected["text"] and received["nonce"] == expected["nonce"]
                assert received["from"] == alice["user"]["id"] and received["to"] == bob["user"]["id"]

            def message(text, nonce):
                return {"type": "send", "to": bob["user"]["id"], "text": text, "nonce": nonce}

            for size in (124, 125, 126, 127, 255, 256, 4096):
                value = message("", f"size-{size}")
                value["text"] = "x" * (size - len(encode(value)))
                assert len(encode(value)) == size
                await sender.send(frame(encode(value)), split=True)
                await delivered(value)
            checks.append("Masked payloads at 7/16-bit length boundaries with partial TCP reads")

            batch = [message(f"coalesced-{i}", f"batch-{i}") for i in range(24)]
            await sender.send(b"".join(frame(encode(value)) for value in batch))
            for value in batch:
                await delivered(value)
            checks.append("24 consecutive frames in one write, each delivered exactly once in order")

            value = message("Fragmented مرحباً 👋 message", "fragmented")
            payload = encode(value)
            split = payload.index("👋".encode()) + 2
            # Split within a UTF-8 code point and interleave a control frame.
            await sender.send(frame(payload[:split], fin=False), split=True)
            await sender.send(frame(b"probe", opcode=9))
            await sender.pong(b"probe")
            await sender.send(frame(b"", opcode=0, fin=False))
            await sender.send(frame(payload[split:], opcode=0), split=True)
            await delivered(value)
            checks.append("Fragmented Unicode, empty continuation, and interleaved ping/pong")

            await sender.send(frame(b"", opcode=9), split=True)
            await sender.pong(b"")
            await sender.send(frame(b""))
            assert (await sender.event("error"))["error"] == "Invalid JSON"
            value = message("Still synchronized", "after-empty")
            await sender.send(frame(encode(value)))
            await delivered(value)
            checks.append("Zero-length control/data frames do not consume the following frame")

            # A canonical 64-bit length exceeds this app's frame limit. It must
            # close the connection, never deliver a truncated application message.
            await sender.send(frame(b"x" * 65536))
            async with asyncio.timeout(5):
                while True:
                    opcode, payload = await sender.receive()
                    if opcode == 8:
                        break
                    assert opcode == 1 and json.loads(payload)["type"] == "users", (opcode, payload)
            assert recipient.messages.empty()
            checks.append("64-bit frame length rejected by the app limit without partial delivery")
        finally:
            await sender.close()
            await recipient.close()
    return {"language": app, "passed": True, "checks": checks}


async def main(args):
    results = []
    for app in args.apps:
        result = await check(app)
        results.append(result)
        print(f"{app}: frame tests PASS ({len(result['checks'])} groups)", flush=True)
    destination = ROOT / args.output
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "frames.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", nargs="+", choices=list(BINARIES), default=DEFAULT_APPS)
    parser.add_argument("--output", default=".build/validation/frames")
    uvloop.run(main(parser.parse_args()))
