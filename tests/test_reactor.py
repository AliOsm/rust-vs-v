"""Adversarial TCP tests for the opt-in event-driven chat transport."""
import argparse
import asyncio
import json
import socket
import struct
import time

import aiohttp
import uvloop

from common import ROOT, ChatClient, Server, register
from test_frames import RawWebSocket, encode, frame


async def closed(raw, code):
    async with asyncio.timeout(5):
        while True:
            opcode, payload = await raw.receive()
            if opcode == 8:
                assert len(payload) >= 2
                actual = struct.unpack("!H", payload[:2])[0]
                assert actual == code, (actual, code, payload)
                return
            assert opcode == 1 and json.loads(payload)["type"] == "users"


async def auth(raw, account):
    await raw.send(frame(encode({"type": "auth", "token": account["token"]})))
    await raw.event("ready")


async def check(app):
    checks = []
    started = time.monotonic()
    async with Server(app) as server, aiohttp.ClientSession() as http:
        alice = await register(http, server.url, "reactor_alice")
        bob = await register(http, server.url, "reactor_bob")
        charlie = await register(http, server.url, "reactor_charlie")
        malformed = [
            (b"\x81\x01x", 1002),  # missing client mask
            (frame(b"x", rsv=0x40), 1002),
            (frame(b"x", opcode=3), 1002),
            (frame(b"x", opcode=9, fin=False), 1002),
            (frame(b"x" * 126, opcode=9), 1002),
            (frame(b"x", opcode=0), 1002),
            (frame(b"\xff"), 1007),
            (frame(b"x", opcode=8), 1002),
            (frame(struct.pack("!H", 1005), opcode=8), 1002),
            (frame(struct.pack("!H", 1000) + b"\xff", opcode=8), 1007),
            (b"\x81\xfe\x00\x01abcd", 1002),  # noncanonical 16-bit length
            (b"\x81\xff" + struct.pack("!Q", 2**63) + b"abcd", 1002),
            (b"\x81\xfe" + struct.pack("!H", 16385) + b"abcd", 1009),
            (frame(b"x" * 10000, fin=False) + frame(b"y" * 7000, opcode=0), 1009),
            (frame(b"x", fin=False) + frame(b"y"), 1002),
        ]
        for wire, expected in malformed:
            raw = await RawWebSocket.open(server.url)
            try:
                await raw.send(wire)
                await closed(raw, expected)
            finally:
                await raw.close()
        checks.append("15 malformed/oversized frame sequences rejected with the expected close code")

        for payload in (b"", struct.pack("!H", 1000) + "bye 👋".encode()):
            raw = await RawWebSocket.open(server.url)
            try:
                await raw.send(frame(payload, opcode=8))
                assert await asyncio.wait_for(raw.receive(), 5) == (8, payload)
            finally:
                await raw.close()
        checks.append("Empty and Unicode close handshakes echoed exactly")

        # An incomplete frame must not occupy a worker or block other sockets.
        stalled = await RawWebSocket.open(server.url)
        active = await ChatClient.connect(http, server.url, alice)
        recipient = await ChatClient.connect(http, server.url, charlie)
        try:
            await stalled.send(b"\x81")
            await active.send(charlie["user"]["id"], "no head-of-line blocking", "stalled")
            echo, delivered = await asyncio.wait_for(asyncio.gather(active.receive(), recipient.receive()), 2)
            assert echo == delivered and delivered["nonce"] == "stalled"
        finally:
            await stalled.close()
            await active.close()
            await recipient.close()
        checks.append("A stalled partial header leaves unrelated users responsive")

        for i in range(80):
            raw = await RawWebSocket.open(server.url)
            try:
                await auth(raw, alice)
                await raw.send(frame(encode({"type": "ping"})))
                assert (await raw.event("pong"))["type"] == "pong"
            finally:
                await raw.close()
            await asyncio.sleep(.002)
        checks.append("80 connect/auth/ping/abrupt-disconnect cycles exercise descriptor reuse")

        before = server.process.num_threads()
        sockets = [await RawWebSocket.open(server.url) for _ in range(64)]
        try:
            await asyncio.sleep(.05)
            after = server.process.num_threads()
            assert after == before, (before, after)
        finally:
            for raw in sockets:
                await raw.close()
        checks.append(f"64 extra connections create no threads ({before} before, {after} after)")

        slow = await RawWebSocket.open(server.url)
        await auth(slow, bob)
        transport = slow.writer.transport
        slow.writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        transport.pause_reading()
        active = await ChatClient.connect(http, server.url, alice)
        recipient = await ChatClient.connect(http, server.url, charlie)
        try:
            for batch in range(64):
                for i in range(32):
                    await active.send(bob["user"]["id"], "s" * 4096, f"slow-{batch}-{i}")
                for i in range(32):
                    echo = await active.receive()
                    assert echo["nonce"] == f"slow-{batch}-{i}"
            async with asyncio.timeout(8):
                while True:
                    async with http.get(server.url + "/api/users", headers={"Authorization": "Bearer " + alice["token"]}) as response:
                        users = (await response.json())["users"]
                    if not next(user for user in users if user["id"] == bob["user"]["id"])["online"]:
                        break
                    await asyncio.sleep(.05)
            await active.send(charlie["user"]["id"], "healthy after slow peer", "healthy")
            echo, delivered = await asyncio.gather(active.receive(), recipient.receive())
            assert echo == delivered and delivered["nonce"] == "healthy"
        finally:
            transport.resume_reading()
            await slow.close()
            await active.close()
            await recipient.close()
        checks.append("Paused reader is disconnected under bounded backpressure; healthy peers keep delivering")

        # Fresh unauthenticated connection must expire without any traffic.
        raw = await RawWebSocket.open(server.url)
        try:
            async with asyncio.timeout(7):
                assert await raw.reader.read(1) == b""
        finally:
            await raw.close()
        checks.append("Unauthenticated idle connection expires after the 5-second read deadline")
    return dict(language=app, passed=True, seconds=time.monotonic() - started, checks=checks)


async def main(args):
    result = await check(args.app)
    out = ROOT / args.output
    out.mkdir(parents=True, exist_ok=True)
    (out / "reactor-adversarial.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"{args.app}: adversarial tests PASS ({len(result['checks'])} groups)", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="v")
    parser.add_argument("--output", default=".build/validation/reactor")
    uvloop.run(main(parser.parse_args()))
