"""Check signal shutdown and connection churn of the local hardened reactor app."""
import argparse
import asyncio
import json
import os
import signal
import struct
import time

import aiohttp
import uvloop

from common import ROOT, ChatClient, Server, register
from test_frames import RawWebSocket, encode, frame
from test_reactor import auth


async def shutdown_case(app, sig, mode):
    async with Server(app) as server, aiohttp.ClientSession() as http:
        peers = []
        if mode != "empty":
            accounts = [await register(http, server.url, f"shutdown_{i}") for i in range(16)]
            for account in accounts:
                peer = await RawWebSocket.open(server.url)
                await auth(peer, account)
                peers.append(peer)

        async def consume(peer, index):
            messages, closed = [], False
            async with asyncio.timeout(5):
                while True:
                    opcode, payload = await peer.receive()
                    if opcode == 8:
                        assert not closed
                        assert struct.unpack("!H", payload[:2])[0] == 1001, payload
                        closed = True
                        if mode == "cooperative":
                            await peer.send(frame(payload, opcode=8))
                        assert await peer.reader.read(1) == b""
                        break
                    assert opcode == 1
                    event = json.loads(payload)
                    if event["type"] == "message":
                        message = event["message"]
                        assert message["text"] == "in-flight shutdown"
                        assert message["to"] == accounts[1]["user"]["id"]
                        assert index in (0, 1)
                        messages.append(message["nonce"])
                    else:
                        assert event["type"] == "users"
            assert len(messages) == len(set(messages))
            return dict(close_code=1001, messages_before_close=len(messages))

        tasks = [asyncio.create_task(consume(peer, i)) for i, peer in enumerate(peers)]
        try:
            if peers:
                await peers[0].send(b"".join(frame(encode(dict(type="send", to=accounts[1]["user"]["id"],
                    text="in-flight shutdown", nonce=str(i)))) for i in range(32)))
            started = time.monotonic()
            os.kill(server.child.pid, sig)
            results = await asyncio.gather(*tasks)
            code = await asyncio.to_thread(server.child.wait, timeout=5)
            elapsed = time.monotonic() - started
            assert code == 0, server.log_path.read_text()
            assert elapsed < 4, elapsed
            if mode == "unresponsive":
                assert elapsed >= .9, elapsed
            return dict(signal=sig.name, mode=mode, exit_code=code, seconds=elapsed, peers=results)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*(peer.close() for peer in peers))


async def churn(app, rounds):
    samples = []
    verified = 0
    async with Server(app) as server, aiohttp.ClientSession() as http:
        accounts = [await register(http, server.url, f"churn_{i}") for i in range(32)]
        initial_fds, initial_threads = server.process.num_fds(), server.process.num_threads()
        for turn in range(rounds):
            peers = []
            try:
                for account in accounts:
                    peers.append(await ChatClient.connect(http, server.url, account))
                for start in range(0, len(peers), 2):
                    await peers[start].send(accounts[start + 1]["user"]["id"], "churn-" + "x" * 1000, f"{turn}:{start}")
                for start in range(0, len(peers), 2):
                    echo, received = await asyncio.gather(peers[start].receive(), peers[start + 1].receive())
                    assert echo == received and echo["nonce"] == f"{turn}:{start}"
                    verified += 1
            finally:
                await asyncio.gather(*(peer.close() for peer in peers))
            # Wait for all deferred close callbacks, not just the client-side close.
            for _ in range(100):
                if server.process.num_fds() <= initial_fds + 1:
                    break
                await asyncio.sleep(.01)
            sample = dict(round=turn + 1, fds=server.process.num_fds(),
                          threads=server.process.num_threads(), rss_mib=server.process.memory_info().rss / 2**20)
            assert sample["fds"] <= initial_fds + 1, sample
            assert sample["threads"] == initial_threads, sample
            samples.append(sample)
        return dict(connections=rounds * len(accounts), verified_messages=verified,
                    initial_fds=initial_fds, initial_threads=initial_threads, samples=samples)


async def main(args):
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    result = dict(app=args.app, shutdown=[], passed=False)
    path = output / "lifecycle.json"

    def save():
        path.write_text(json.dumps(result, indent=2) + "\n")

    for sig in (signal.SIGINT, signal.SIGTERM):
        for mode in ("empty", "cooperative", "unresponsive"):
            row = await shutdown_case(args.app, sig, mode)
            result["shutdown"].append(row)
            print(f"{sig.name} {mode}: exit 0 in {row['seconds']:.3f}s", flush=True)
            save()
    result["churn"] = await churn(args.app, args.rounds)
    result["passed"] = True
    save()
    print(f"{result['churn']['connections']} connection cycles: no descriptor or thread growth", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="v")
    parser.add_argument("--rounds", type=int, default=40)
    parser.add_argument("--output", default=".build/validation/reactor_lifecycle")
    uvloop.run(main(parser.parse_args()))
