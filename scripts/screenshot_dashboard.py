"""Screenshot the demo dashboard for the README (docs/img/dashboard.png).

Rebuilds the static demo (see build_demo.py — real UI, fictional data, no
backend), opens it in headless Chromium, drives a short chat and captures
the image.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from build_demo import build

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"

QUESTION = "When is my Databases exam and what should I revise?"


def wait_idle(page):
    page.wait_for_function(
        "document.getElementById('statusTag').textContent === 'AWAITING COMMAND'",
        timeout=60000,
    )


def main() -> None:
    demo = build()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1720, "height": 1040}, device_scale_factor=2)
        page.goto(demo.as_uri())
        page.wait_for_selector("#boot.done", timeout=30000)
        wait_idle(page)  # daily brief finished typing
        page.fill("#input", QUESTION)
        page.keyboard.press("Enter")
        wait_idle(page)  # chat reply finished typing
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "dashboard.png"))
        browser.close()
    print(f"saved {OUT / 'dashboard.png'}")


if __name__ == "__main__":
    main()
