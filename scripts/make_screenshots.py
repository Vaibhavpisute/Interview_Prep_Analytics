"""Create the evidence screenshots in docs/screenshots/ (needs: pip install playwright).

It drives the locally installed Google Chrome, so no browser download is needed.

  python scripts/make_screenshots.py dashboard   # Streamlit dashboard (start it first)
  python scripts/make_screenshots.py cli         # `ipa demo` console output rendered to PNG
  python scripts/make_screenshots.py ci          # GitHub Actions page of the public repo
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
REPO_URL = "https://github.com/Vaibhavpisute/Interview_Prep_Analytics"


def dashboard(url: str = "http://localhost:8599/") -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 2000}, color_scheme="light")
        page.goto(url)
        page.wait_for_selector("[data-testid='stMetric']", timeout=60000)
        page.wait_for_selector("canvas", timeout=60000)
        page.wait_for_timeout(4000)  # let the charts finish drawing
        page.screenshot(path=str(OUT / "dashboard.png"))
        browser.close()


def cli() -> None:
    import logging

    from rich.console import Console

    from ipa.demo import run_demo
    from ipa.logging_setup import setup_logging

    setup_logging("CRITICAL")
    logging.getLogger("ipa").setLevel(logging.CRITICAL)
    console = Console(record=True, width=110, force_terminal=True, color_system="truecolor")
    os.chdir(ROOT)  # relative paths keep the screenshot free of local folder names
    run_demo(console, "sqlite:///data/demo.db", True, Path("data/raw"),
             Path("data/jd/sample_jd.txt"), Path("output"))  # fmt: skip
    svg_text = console.export_svg(title="ipa demo")
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=1.5)
        page.set_content(f"<html><body style='margin:0;background:#fff'>{svg_text}</body></html>")
        page.wait_for_timeout(1500)
        page.locator("svg").first.screenshot(path=str(OUT / "cli_demo.png"), timeout=60000)
        browser.close()


def ci() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1300, "height": 900}, color_scheme="light")
        page.goto(f"{REPO_URL}/actions/workflows/ci.yml")
        page.wait_for_selector("[data-testid='workflow-runs-list'], .Box-row", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "ci_green.png"))
        browser.close()


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    {"dashboard": dashboard, "cli": cli, "ci": ci}[sys.argv[1]]()
    print("done", sys.argv[1])
