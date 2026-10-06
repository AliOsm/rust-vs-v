"""Check malformed-input recovery, buffer growth, delayed delivery, and retained history."""
import argparse
import asyncio
import json
import os

import aiohttp
import uvloop

from common import BINARIES, DEFAULT_APPS, ROOT, ChatClient, Server, register


async def check(app):
    checked, deliveries = [], 0
    async with Server(app) as server, aiohttp.ClientSession() as http:
        alice = await register(http, server.url, "json_alice")
        bob = await register(http, server.url, "json_bob")
        a = await ChatClient.connect(http, server.url, alice)
        b = await ChatClient.connect(http, server.url, bob)
        malformed = ['[0', '[true', '{"type":"ping","extra":{}',
                     '{"type":"send","to":2,"text":"missing parent"',
                     '{"type":"ping","extra":[1]', '{"type":"send","text":"bad\\x"}']
        for wire in malformed:
            await a.ws.send_str(wire)
            assert await asyncio.wait_for(a.errors.get(), 3) == "Invalid JSON", wire
            await a.send(bob["user"]["id"], "after malformed input", wire[:20])
            assert await a.receive() == await b.receive()
            deliveries += 2
        checked.append("Truncated containers and invalid escapes reject promptly; the connection recovers")
        texts = ['short', 'A' * 4096, 'é' * 2048, '😀' * 1024, '\x00' * 1000,
                 '"\\\n\r\t' * 400, 'مرحبا — retained Unicode']
        expected = []
        for start in range(0, 120, 8):
            batch = []
            for index in range(start, start + 8):
                message = dict(type="send", to=bob["user"]["id"], text=texts[index % len(texts)],
                               nonce=f"buffer-{index}")
                if index % 8 == 0:
                    message["ignored"] = {"tokens": list(range(1024))}
                wire = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
                assert len(wire.encode()) <= 16384
                await a.ws.send_str(wire)
                batch.append(message)
            # Read after several encodes have reused the same worker's storage.
            for message in batch:
                echoed, received = await asyncio.gather(a.receive(), b.receive())
                assert echoed == received
                assert received["text"] == message["text"] and received["nonce"] == message["nonce"]
                expected.append(received)
                deliveries += 2
        checked.append("Growing/shrinking JSON buffers preserve delayed sender echoes and recipient deliveries")
        checked.append("Unknown nested fields grow token storage; Unicode, control escapes and byte limits survive reuse")
        async with http.get(server.url + f"/api/messages/{bob['user']['id']}",
                            headers={"Authorization": "Bearer " + alice["token"]}) as response:
            assert response.status == 200
            assert (await response.json())["messages"] == expected[-100:]
        checked.append("The last 100 history messages retain their exact contents after subsequent reuse")
        await a.close()
        await b.close()
    return dict(app=app, passed=True, deliveries=deliveries, checks=checked)


async def main(args):
    assert os.environ.get("RUST_VS_V_RESOURCE_GUARD"), "Run through scripts/resource_guard.py"
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for app in args.apps:
        row = await check(app)
        rows.append(row)
        (output / "json-contract.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(f"{app}: {len(row['checks'])} groups, {row['deliveries']} verified deliveries", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", nargs="+", choices=list(BINARIES),
                        default=DEFAULT_APPS)
    parser.add_argument("--output", default=".build/validation/json_buffers")
    uvloop.run(main(parser.parse_args()))
