"""Check blank-text rules, borrowed-frame recovery, and retained message contents."""
import asyncio

import aiohttp
from common import Server, ChatClient, register


async def check(app):
    deliveries = 0
    async with Server(app) as server, aiohttp.ClientSession() as http:
        alice = await register(http, server.url, "copy_alice")
        bob = await register(http, server.url, "copy_bob")
        a = await ChatClient.connect(http, server.url, alice)
        b = await ChatClient.connect(http, server.url, bob)
        for blank in ("", " ", "\t\r\n", " " * 4096, " \t\r\n" * 256):
            await a.send(bob["user"]["id"], blank, "blank")
            error = await asyncio.wait_for(a.errors.get(), 3)
            assert error == "Message must contain 1–4096 UTF-8 bytes; nonce at most 64 bytes", error
        expected = []
        for index, text in enumerate((" leading ", "\tcontent\r\n", "\v", "\f", "\u00a0",
                                       "\u2003", "\x00", "é 😀", " " * 4095 + "x",
                                       "x" + " " * 4095, "\t" * 1024 + "x")):
            await a.send(bob["user"]["id"], text, f"copy-{index}")
            echoed, received = await asyncio.gather(a.receive(), b.receive())
            assert echoed == received and received["text"] == text
            expected.append(received)
            deliveries += 2
        for wire in ("", '"' + "\t" * 8, "\t" * 8 + "[", '{"type":"send","to":' + "\t" * 8 + "{}}"):
            await a.ws.send_str(wire)
            assert await asyncio.wait_for(a.errors.get(), 3) == "Invalid JSON"
            await a.send(bob["user"]["id"], "after invalid input", "recovered")
            echoed, received = await asyncio.gather(a.receive(), b.receive())
            assert echoed == received and received["text"] == "after invalid input"
            expected.append(received)
            deliveries += 2
        async with http.get(server.url + f"/api/messages/{bob['user']['id']}",
                            headers={"Authorization": "Bearer " + alice["token"]}) as response:
            assert response.status == 200 and (await response.json())["messages"] == expected
        await a.close()
        await b.close()
    return dict(passed=True, deliveries=deliveries, checks=[
        "Only space/tab/CR/LF-only text is blank, including empty and boundary-length input",
        "Accepted leading/trailing whitespace, Unicode and control bytes retain exact content",
        "Empty and tab-heavy malformed frames reject promptly; subsequent messages recover",
        "History retains values after frame and decoder reuse"])
