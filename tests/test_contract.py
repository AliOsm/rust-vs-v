"""The same integration contract runs against both release executables."""
import asyncio
import argparse
import hashlib
import json
import time

import aiohttp
import uvloop

from common import BINARIES, DEFAULT_APPS, ROOT, ChatClient, Server, register


async def check(language):
    checks = []
    async with Server(language) as server, aiohttp.ClientSession() as http:
        url = server.url

        async def request(method, path, expected, **kwargs):
            async with http.request(method, url + path, **kwargs) as response:
                body = await response.json()
                assert response.status == expected, (method, path, expected, response.status, body)
                return body

        await request("GET", "/health", 200)
        for asset in ("index.html", "style.css", "app.js", "inter-latin.woff2"):
            async with http.get(url + ("/" if asset == "index.html" else "/" + asset)) as r:
                assert r.status == 200
                assert await r.read() == (ROOT / "rust-app/public" / asset).read_bytes()
        checks.append("Identical embedded frontend and health endpoint")
        for name in ("ab", "a" * 25, "bad name", "<script>", "مرحبا", ""):
            await request("POST", "/api/register", 400, json={"username": name})
        await request("POST", "/api/register", 400, data="not-json")
        alice = await register(http, url, "Alice")
        bob = await register(http, url, "Bob")
        charlie = await register(http, url, "Charlie")
        await request("POST", "/api/register", 409, json={"username": "aLiCe"})
        assert len(alice["token"]) == 64 and alice["token"] != bob["token"]
        await request("GET", "/api/users", 401)
        await request("GET", "/api/messages/1", 401)
        await request("GET", "/api/users", 401, headers={"Authorization": "Bearer wrong"})
        await request("POST", "/api/register", 403, json={"username": "Mallory"}, headers={"Origin": "https://evil.example"})
        try:
            await http.ws_connect(url + "/ws", origin="https://evil.example")
            raise AssertionError("Foreign origin accepted")
        except aiohttp.WSServerHandshakeError as error:
            assert error.status == 403
        async with http.ws_connect(url + "/ws") as unauth:
            await unauth.send_json({"type": "auth", "token": "wrong"})
            async with asyncio.timeout(5):
                async for _ in unauth:
                    pass
            assert unauth.close_code == 1008
        checks.append("Registration validation, unique names, private tokens, auth and origin rejection")

        a = await ChatClient.connect(http, url, alice)
        b = await ChatClient.connect(http, url, bob)
        c = await ChatClient.connect(http, url, charlie)
        aid, bid, cid = (x["user"]["id"] for x in (alice, bob, charlie))
        auth = {"Authorization": "Bearer " + alice["token"]}
        users = await request("GET", "/api/users", 200, headers=auth)
        assert all(user["online"] for user in users["users"])
        text = 'Hello مرحباً 👋 <img src=x onerror=alert(1)>\n"quoted" & literal'
        await a.send(bid, text, "first")
        received, echoed = await b.receive(), await a.receive()
        assert received == echoed
        assert received["text"] == text and received["from"] == aid and received["to"] == bid
        assert received["nonce"] == "first" and abs(received["sent_at"] - time.time() * 1000) < 5000
        await b.send(aid, "A reply", "reply")
        assert (await a.receive()) == (await b.receive())
        await asyncio.sleep(.05)
        assert c.messages.empty(), "Private message leaked to a third user"
        checks.append("Bidirectional delivery, sender acknowledgement, Unicode and private routing")

        await a.ws.send_str("{")
        assert await asyncio.wait_for(a.errors.get(), 3) == "Invalid JSON"
        for payload in ({"type": "unknown"}, {"type": "send", "to": aid, "text": "self"},
                        {"type": "send", "to": 99999, "text": "nobody"},
                        {"type": "send", "to": bid, "text": " "},
                        {"type": "send", "to": bid, "text": "é" * 2049},
                        {"type": "send", "to": bid, "text": "valid", "nonce": "x" * 65}):
            await a.ws.send_json(payload)
            assert await asyncio.wait_for(a.errors.get(), 3)
        await a.send(bid, "é" * 2048, "max")
        assert (await b.receive())["text"] == "é" * 2048
        await a.receive()
        checks.append("Malformed input, invalid recipients and UTF-8 byte limits")

        await b.close()
        for _ in range(100):
            users = await request("GET", "/api/users", 200, headers=auth)
            if not next(u for u in users["users"] if u["id"] == bid)["online"]:
                break
            await asyncio.sleep(.01)
        else:
            raise AssertionError("Offline presence not updated")
        await a.send(bid, "While you were away", "offline")
        await a.receive()
        b = await ChatClient.connect(http, url, bob)
        history = await request("GET", f"/api/messages/{aid}", 200, headers={"Authorization": "Bearer " + bob["token"]})
        assert history["messages"][-1]["nonce"] == "offline"
        private = await request("GET", f"/api/messages/{aid}", 200, headers={"Authorization": "Bearer " + charlie["token"]})
        assert private["messages"] == []
        await request("GET", "/api/messages/99999", 404, headers=auth)
        checks.append("Presence, reconnect, offline history and history isolation")

        for i in range(110):
            await a.send(bid, f"history {i}", str(i))
            assert (await b.receive()) == (await a.receive())
        history = await request("GET", f"/api/messages/{bid}", 200, headers=auth)
        assert len(history["messages"]) == 100
        assert [m["text"] for m in history["messages"]] == [f"history {i}" for i in range(10, 110)]
        checks.append("Bounded ordered history after wraparound")

        # Multiple sockets belonging to the same account receive the same messages.
        a2 = await ChatClient.connect(http, url, alice)
        await b.send(aid, "Both tabs", "tabs")
        assert (await a.receive()) == (await a2.receive()) == (await b.receive())
        await a2.close()
        await asyncio.gather(a.close(), b.close(), c.close())
        checks.append("Multiple sessions for one user")

        async def concurrent_pair(i):
            x = await register(http, url, f"pair_{i}_a")
            y = await register(http, url, f"pair_{i}_b")
            left = await ChatClient.connect(http, url, x)
            right = await ChatClient.connect(http, url, y)
            try:
                last = 0
                for j in range(120):
                    sender, receiver = (left, right) if j % 2 else (right, left)
                    await sender.send(receiver.account["user"]["id"], f"{i}/{j}", f"{i}_{j}")
                    delivered, echo = await receiver.receive(), await sender.receive()
                    assert delivered == echo and delivered["text"] == f"{i}/{j}"
                    assert delivered["from"] == sender.account["user"]["id"]
                    assert delivered["id"] > last
                    last = delivered["id"]
            finally:
                await asyncio.gather(left.close(), right.close())
        await asyncio.gather(*(concurrent_pair(i) for i in range(12)))
        checks.append("12 concurrent conversations, 1,440 verified messages, no cross-talk")
    return {"language": language, "checks": checks, "passed": True}


async def main(args):
    # This is also a guard against accidental frontend drift.
    for source in (ROOT / "rust-app/public").iterdir():
        assert source.read_bytes() == (ROOT / "v-app/public" / source.name).read_bytes(), source.name
    results = []
    for language in args.apps:
        result = await check(language)
        results.append(result)
        print(f"{language}: PASS ({len(result['checks'])} groups)", flush=True)
    target = ROOT / args.output
    target.mkdir(parents=True, exist_ok=True)
    (target / "correctness.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", nargs="+", choices=list(BINARIES), default=DEFAULT_APPS)
    parser.add_argument("--output", default=".build/validation/contract")
    uvloop.run(main(parser.parse_args()))
