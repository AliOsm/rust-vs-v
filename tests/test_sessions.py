"""Check session limits, eight-way delivery, authentication isolation and history."""
import argparse
import asyncio
import json

import aiohttp
import uvloop

from common import BINARIES, ROOT, ChatClient, Server, register


async def rejected(http, url, payload):
    async with http.ws_connect(url + "/ws") as ws:
        await ws.send_json(payload)
        errors = []
        async with asyncio.timeout(5):
            async for frame in ws:
                if frame.type == aiohttp.WSMsgType.TEXT:
                    event = json.loads(frame.data)
                    if event["type"] == "error":
                        errors.append(event["error"])
                    assert event["type"] not in ("message", "ready")
        assert ws.close_code == 1008, ws.close_code
        # Rust closes an excess session directly; V sends an error first.
        assert all(error in ("Unauthorized", "Session limit reached") for error in errors), errors


async def check(app):
    checks, delivered = [], 0
    async with Server(app) as server, aiohttp.ClientSession() as http:
        accounts = [await register(http, server.url, name) for name in ("session_alice", "session_bob", "outsider")]
        alice, bob, stranger = accounts
        outsider = await ChatClient.connect(http, server.url, stranger)
        for round_number in range(6):
            # Alternate accounts and connection order across reactor workers.
            clients = [await ChatClient.connect(http, server.url, accounts[(i + round_number) % 2])
                       for i in range(8)]
            try:
                for account in (alice, bob):
                    await rejected(http, server.url, {"type": "auth", "token": account["token"]})
                for i in range(24):
                    sender = clients[i % len(clients)]
                    target = bob if sender.account == alice else alice
                    nonce = f"{round_number}/{i}"
                    text = f"all eight sessions {nonce} — مرحباً 👋"
                    await sender.send(target["user"]["id"], text, nonce)
                    messages = await asyncio.gather(*(client.receive() for client in clients))
                    assert all(message == messages[0] for message in messages)
                    assert messages[0]["text"] == text and messages[0]["nonce"] == nonce
                    assert messages[0]["from"] == sender.account["user"]["id"]
                    assert messages[0]["to"] == target["user"]["id"]
                    delivered += len(messages)
                assert outsider.messages.empty(), "Private message reached another account"
                # An authenticated connection cannot change identity by sending auth again.
                sender = clients[0]
                target = bob if sender.account == alice else alice
                await sender.ws.send_json({"type": "auth", "token": target["token"]})
                assert await asyncio.wait_for(sender.errors.get(), 3) == "Unknown message type"
                await sender.send(target["user"]["id"], "identity unchanged", f"identity-{round_number}")
                messages = await asyncio.gather(*(client.receive() for client in clients))
                assert all(message == messages[0] for message in messages)
                assert messages[0]["from"] == sender.account["user"]["id"]
                delivered += len(messages)
            finally:
                await asyncio.gather(*(client.close() for client in clients))
            # Presence becoming offline confirms that both workers removed all sessions.
            async with asyncio.timeout(5):
                while True:
                    async with http.get(server.url + "/api/users",
                        headers={"Authorization": "Bearer " + alice["token"]}) as response:
                        users = (await response.json())["users"]
                    if all(not user["online"] for user in users if user["id"] in (1, 2)):
                        break
                    await asyncio.sleep(.01)
            await rejected(http, server.url, {"type": "send", "to": 2, "text": "unauthenticated"})
        checks.extend(["Six rounds of eight simultaneous sessions receive every delivery exactly",
                       "Both accounts reject a fifth session without disrupting existing sessions",
                       "Reauthentication cannot change a connection's identity",
                       "Fresh connections do not inherit authentication after descriptor reuse",
                       "A third account receives no private messages"])
        history = []
        for account, other in ((alice, bob), (bob, alice)):
            async with http.get(server.url + f"/api/messages/{other['user']['id']}",
                headers={"Authorization": "Bearer " + account["token"]}) as response:
                assert response.status == 200
                history.append((await response.json())["messages"])
        assert history[0] == history[1] and len(history[0]) == 100
        assert history[0][0]["nonce"] == "2/0"
        assert history[0][-1]["nonce"] == "identity-5"
        assert all(a["id"] < b["id"] for a, b in zip(history[0], history[0][1:]))
        checks.append("Both directions return the same last 100 messages in order")
        await outsider.close()
    return dict(app=app, passed=True, deliveries=delivered, checks=checks)


async def main(args):
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for app in args.apps:
        row = await check(app)
        rows.append(row)
        (output / "sessions.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(f"{app}: {len(row['checks'])} groups; {row['deliveries']} verified deliveries", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", nargs="+", choices=list(BINARIES), default=["v"])
    parser.add_argument("--output", default=".build/validation/sessions")
    uvloop.run(main(parser.parse_args()))
