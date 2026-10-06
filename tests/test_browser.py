"""Browser checks use the installed Chrome, or Playwright's Chromium fallback."""
import asyncio
import argparse
import json
import os
import shutil

import uvloop
from playwright.async_api import async_playwright, expect

from common import BINARIES, ROOT, Server


async def main(args):
    results = []
    destination = ROOT / args.output
    destination.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        chrome = os.getenv("CHROME_BIN") or shutil.which("google-chrome") or shutil.which("chromium")
        browser = await playwright.chromium.launch(executable_path=chrome, headless=True)
        try:
            for language in args.apps:
                async with Server(language) as server:
                    context = await browser.new_context(viewport={"width": 1440, "height": 980})
                    errors = []
                    context.on("page", lambda page: page.on("pageerror", lambda error: errors.append(str(error))))
                    alice, bob = await context.new_page(), await context.new_page()
                    await alice.goto(server.url)
                    await alice.screenshot(path=str(destination / f"{language}-welcome.png"))
                    await alice.get_by_label("Your username").fill("alice_browser")
                    await alice.get_by_role("button", name="Join the conversation").click()
                    await expect(alice.locator("#connection-status")).to_have_text("Connected")
                    await bob.goto(server.url)
                    await bob.get_by_label("Your username").fill("bob_browser")
                    await bob.get_by_role("button", name="Join the conversation").click()
                    await expect(bob.locator("#connection-status")).to_have_text("Connected")
                    await alice.locator("#people button").filter(has_text="bob_browser").click()
                    await bob.locator("#people button").filter(has_text="alice_browser").click()
                    await alice.get_by_label("Message", exact=True).fill("Hey Bob! Ready to try Relay?")
                    await alice.get_by_role("button", name="Send").click()
                    await expect(bob.locator(".bubble").last).to_have_text("Hey Bob! Ready to try Relay?")
                    await bob.get_by_label("Message", exact=True).fill("I’m here. Everything arrives right away.")
                    await bob.get_by_label("Message", exact=True).press("Enter")
                    await expect(alice.locator(".bubble").last).to_have_text("I’m here. Everything arrives right away.")
                    await alice.get_by_label("Message", exact=True).fill("Perfect. Let’s keep the conversation going.")
                    await alice.get_by_label("Message", exact=True).press("Enter")
                    await expect(bob.locator(".bubble").last).to_have_text("Perfect. Let’s keep the conversation going.")
                    await alice.screenshot(path=str(destination / f"{language}-desktop.png"))
                    # Untrusted text must be rendered literally, never as markup.
                    literal = '<img src=x onerror="window.xss=true"> مرحباً'
                    await bob.get_by_label("Message", exact=True).fill(literal)
                    await bob.get_by_role("button", name="Send").click()
                    await expect(alice.locator(".bubble").last).to_have_text(literal)
                    assert await alice.evaluate("window.xss === undefined && !document.querySelector('.bubble img')")
                    await alice.reload()
                    await expect(alice.locator("#connection-status")).to_have_text("Connected")
                    await alice.locator("#people button").filter(has_text="bob_browser").click()
                    await expect(alice.locator(".bubble").last).to_have_text(literal)
                    # Exercise the reconnect path and recover conversation history.
                    await alice.evaluate("socket.close()")
                    await expect(alice.locator("#connection-status")).to_have_text("Reconnecting…")
                    await expect(alice.locator("#connection-status")).to_have_text("Connected", timeout=10000)
                    await alice.set_viewport_size({"width": 390, "height": 844})
                    await alice.get_by_role("button", name="People", exact=True).click()
                    await expect(alice.locator("#people-panel")).to_be_visible()
                    await alice.locator("#people button").filter(has_text="bob_browser").click()
                    await expect(alice.locator("#people-panel")).to_be_hidden()
                    assert await alice.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    await alice.get_by_label("Message", exact=True).fill("Sent from the mobile layout.")
                    await alice.get_by_role("button", name="Send").click()
                    await expect(bob.locator(".bubble").last).to_have_text("Sent from the mobile layout.")
                    await alice.screenshot(path=str(destination / f"{language}-mobile.png"))
                    assert not errors, errors
                    results.append({"language": language, "passed": True,
                        "checks": ["registration", "two-user live delivery", "safe text rendering",
                                   "reload and history", "automatic reconnect", "mobile navigation and sending"],
                        "browser": browser.version})
                    print(f"{language}: browser PASS (desktop + mobile)", flush=True)
                    await context.close()
        finally:
            await browser.close()
    (destination / "browser.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apps", nargs="+", choices=list(BINARIES), default=["rust", "v"])
    parser.add_argument("--output", default=".build/validation/browser")
    uvloop.run(main(parser.parse_args()))
