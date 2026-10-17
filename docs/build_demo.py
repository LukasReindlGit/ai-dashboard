"""Build docs/demo.html (the dashboard with made-up data, runs as a static file) and its screenshots.

    python3 docs/build_demo.py      # needs playwright + chromium for the screenshots
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
page = (ROOT.parent / "web" / "index.html").read_text()
mock = (ROOT / "mock.js").read_text()
page = page.replace("{{BASE}}", "").replace("<script>", "<script>\n" + mock + "\n", 1)
(ROOT / "demo.html").write_text(page)
print("wrote", ROOT / "demo.html")

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("playwright not installed, skipping screenshots")

SHOTS = {  # LinkedIn: landscape 1200x627-ish for desktop, 4:5 portrait for mobile
    "screenshot-desktop.png": dict(viewport={"width": 1440, "height": 900}, device_scale_factor=2),
    "screenshot-mobile.png": dict(viewport={"width": 390, "height": 844}, device_scale_factor=3,
                                  is_mobile=True, has_touch=True),
}
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, opts in SHOTS.items():
        pg = b.new_page(**opts)
        pg.goto((ROOT / "demo.html").as_uri())
        pg.wait_for_selector(".card")
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(ROOT / name))
        print("wrote", ROOT / name)
    b.close()
