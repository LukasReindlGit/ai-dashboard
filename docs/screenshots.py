"""Take the README screenshots from the real dashboard, with made-up sessions.

    python3 docs/screenshots.py        # needs tmux, and playwright with chromium

Nothing here is mocked in the page: a throwaway tmux server (its own socket, so your sessions
are left alone) gets 7 sessions in 3 demo projects, running stand-ins for `claude` and
`scripts/batch.py` that print a canned screen and then wait. The real app.py serves them, and
Playwright photographs the real page. Only two things are faked: the login is skipped, and
the session ages are shifted so the cards don't all say "just started".

Writes docs/screenshot-{desktop,mobile,terminal,cron}.png.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

DOCS = Path(__file__).resolve().parent
REPO = DOCS.parent
NOW = int(time.time())
M, H = 60, 3600

# ANSI helpers, in the colours Claude Code uses (app.py maps them onto the dashboard's palette).
E = "\x1b["
dim, bold, white, gray = (lambda t: f"{E}2m{t}{E}0m"), (lambda t: f"{E}1m{t}{E}0m"), (lambda t: f"{E}97m{t}{E}39m"), (lambda t: f"{E}37m{t}{E}39m")
green, red, yellow, blue, orange = (lambda t: f"{E}32m{t}{E}39m"), (lambda t: f"{E}31m{t}{E}39m"), (lambda t: f"{E}33m{t}{E}39m"), (lambda t: f"{E}34m{t}{E}39m"), (lambda t: f"{E}38;5;209m{t}{E}39m")
RULE = gray("─" * 118)
DOT = white("●")


def prompt(text):
    return f"{E}30m{E}40m❯ {E}97m{text}{E}0m"


def tool(name, arg, result):
    return [f"{green('●')} {bold(name)}({arg})", f"  ⎿  {result}"]


def input_box(model="Opus 5.5 · 5h 62% · 7d 38%", mode="auto mode on"):
    return ["", RULE, "❯ ", RULE, "  " + dim(gray(model)), "  " + yellow(f"⏵⏵ {mode}") + dim(gray(" (shift+tab to cycle)"))]


def banner(project):
    return [f" {orange('▐▛███▜▌')}   {bold('Claude Code')} {gray('v2.1.290')}",
            f"{orange('▝▜█████▛▘')}  {gray('Opus 5.5 · Claude Max')}",
            f"  {orange('▘▘ ▝▝')}    {gray('~/projects/' + project)}", ""]


def hm(ago):
    return dt.datetime.fromtimestamp(NOW - ago).strftime("%H:%M:%S")


# ---------------------------------------------------------------- the 7 sessions

# name -> project, kind, screen lines, ages (created / last activity / batch running for, seconds)
SESSIONS = {}


def session(name, project, kind, lines, created, activity, running=None, args=""):
    SESSIONS[name] = dict(project=project, kind=kind, lines=lines, created=created,
                          activity=activity, running=running, args=args)


session("acme-shop", "acme-shop", "claude", created=5 * H + 12 * M, activity=2 * M, lines=[
    *banner("acme-shop"),
    prompt("Rename the old discount fields in the orders table and write the migration"),
    "",
    f"{DOT} I'll rename {white('discount_pct')} → {white('discount_rate')} and {white('promo')} → {white('promo_code')}, then add a migration.",
    "",
    *tool("Update", "db/schema.prisma", "Updated 4 lines"),
    *tool("Write", "db/migrations/0042_rename_discount_fields.sql", "Wrote 18 lines"),
    *tool("Update", "src/orders/totals.ts", "Updated 7 lines"),
    "",
    f"{DOT} The code compiles. I'd like to run the migration against the local database.",
    "",
    RULE,
    f" {bold('Bash command')}",
    "",
    "   npm run db:migrate",
    f"   {gray('Apply pending migrations to the local database')}",
    "",
    f" {white('Do you want to proceed?')}",
    f" {blue('❯ 1. Yes')}",
    "   2. Yes, and don't ask again for npm run db:migrate commands",
    "   3. No, and tell Claude what to do differently (esc)",
])

session("acme-shop-search", "acme-shop", "claude", created=1 * H + 5 * M, activity=4, lines=[
    *banner("acme-shop"),
    prompt("Search results are slow for long queries. Profile it and fix the worst part"),
    "",
    *tool("Bash", "npm run bench -- search", "p95 1.84 s for 6+ word queries (12k products)"),
    *tool("Read", "src/search/rank.ts", "Read 212 lines"),
    "",
    f"{DOT} The ranker re-tokenizes every product for every query. I'll cache the token sets per product.",
    "",
    *tool("Update", "src/search/rank.ts", "Added 21 lines, removed 9 lines"),
    *tool("Bash", "npm run bench -- search", "p95 0.21 s for 6+ word queries (12k products)"),
    "",
    f"{orange('✻')} {orange('Running the search tests…')} {dim(gray('(2m 41s · ↓ 6.3k tokens · esc to interrupt)'))}",
    *input_box(),
])

session("batch-acme-shop", "acme-shop", "batch", created=3 * H + 14 * M, activity=8, running=3 * H + 10 * M,
        args="--max-issues 8", lines=[
    "== Batch: 8 issues, model sonnet, rework first ==",
    f"[{hm(3 * H + 9 * M)}] #41 Show stock count on product cards  [new: sonnet: small UI change]",
    f"[{hm(2 * H + 41 * M)}] #41 -> merged in 27 min: PR #118, 3 files",
    f"[{hm(2 * H + 41 * M)}] #43 Paginate the admin order list  [new: sonnet: plain CRUD]",
    f"[{hm(2 * H + 8 * M)}] #43 -> merged in 33 min: PR #119, 5 files",
    f"[{hm(2 * H + 8 * M)}] #44 Add wishlist sharing link  [new: sonnet: small feature]",
    f"[{hm(98 * M)}] #44 -> merged in 21 min: PR #120, 4 files",
    f"[{hm(98 * M)}] #45 Migrate cart to the new pricing API  [new: opus: touches checkout]",
    f"[{hm(71 * M)}] #45 -> failed in 26 min: 3 cart tests fail against the pricing sandbox",
    f"[{hm(71 * M)}] #47 Fix currency rounding in invoices  [new: sonnet: bug with repro]",
    f"[{hm(39 * M)}] #47 -> merged in 18 min: PR #121, 2 files",
    f"[{hm(14 * M)}] #49 Checkout: show delivery estimate per item  [new: sonnet: UI + API]",
])

session("weather-api", "weather-api", "claude", created=47 * M, activity=3, lines=[
    *banner("weather-api"),
    prompt("Add a /forecast/hourly endpoint with caching and tests"),
    "",
    *tool("Read", "app/routes/forecast.py", "Read 96 lines"),
    *tool("Update", "app/routes/forecast.py", "Added 38 lines"),
    *tool("Update", "app/cache.py", "Added 12 lines, removed 2 lines"),
    *tool("Write", "tests/test_hourly.py", "Wrote 64 lines"),
    "",
    f"{orange('✻')} {orange('Writing tests…')} {dim(gray('(3m 12s · ↓ 4.1k tokens · esc to interrupt)'))}",
    *input_box(),
])

session("batch-weather-api", "weather-api", "batch", created=6 * H + 2 * M, activity=5 * M, running=5 * H + 50 * M,
        args="--max-issues 5", lines=[
    "== Batch: 5 issues, model sonnet ==",
    f"[{hm(5 * H + 48 * M)}] #12 Add a /health endpoint with the cache status  [new: sonnet: small]",
    f"[{hm(5 * H + 30 * M)}] #12 -> merged in 18 min: PR #31, 2 files",
    f"[{hm(5 * H + 30 * M)}] #13 Cache geocoding lookups  [new: sonnet: small feature]",
    f"[{hm(2 * H)}] #13 -> merged in 16 min: PR #32, 3 files",
    f"[{hm(2 * H)}] #15 Rate-limit anonymous requests  [new: sonnet: middleware]",
    f"[{hm(80 * M)}] #15 -> merged in 24 min: PR #33, 4 files",
    f"[{hm(14 * M)}] #16 Return wind gusts in metric and imperial  [new: sonnet: small]",
    f"[{hm(5 * M)}] Usage limit; sleeping 25 min, then resuming #16.",
])

session("notes-app", "notes-app", "claude", created=2 * H + 3 * M, activity=26 * M, lines=[
    *banner("notes-app"),
    prompt("Make the editor toolbar work on small screens"),
    "",
    *tool("Update", "src/editor/Toolbar.tsx", "Added 31 lines, removed 12 lines"),
    *tool("Update", "src/editor/toolbar.css", "Added 18 lines"),
    *tool("Bash", "npx playwright screenshot --viewport-size=390,844 …", "Saved toolbar-mobile.png"),
    *tool("Bash", "~/.claude/skills/dashboard-screenshots/share.sh toolbar-mobile.png", "Shared"),
    "",
    f"{DOT} Done. Below 480 px the toolbar keeps bold, italic and lists, and the rest move into a ⋯ menu.",
    "  The buttons are 44 px tall on touch screens. I shared a screenshot of it on the dashboard.",
    "  All 58 tests pass.",
    "",
    gray("✻ Cooked for 6m 20s"),
    *input_box(),
])

session("batch-notes-app", "notes-app", "batch", created=9 * H + 30 * M, activity=3 * H, lines=[
    "== Batch: 4 issues, model sonnet ==",
    f"[{hm(5 * H + 2 * M)}] #7 Keyboard shortcut for search  [new: sonnet: small]",
    f"[{hm(4 * H + 50 * M)}] #7 -> merged in 12 min: PR #52, 2 files",
    f"[{hm(4 * H + 50 * M)}] #8 Remember the last opened notebook  [new: sonnet: small]",
    f"[{hm(4 * H + 26 * M)}] #8 -> merged in 24 min: PR #53, 3 files",
    f"[{hm(4 * H + 26 * M)}] #9 Dark mode for the settings page  [new: sonnet: css]",
    f"[{hm(4 * H + 14 * M)}] #9 -> merged in 12 min: PR #54, 2 files",
    f"[{hm(4 * H + 14 * M)}] #11 Export notes as Markdown  [new: sonnet: small feature]",
    f"[{hm(3 * H + 55 * M)}] #11 -> merged in 19 min: PR #55, 4 files",
    f"[{hm(3 * H)}] Done: #7, #8, #9, #11.",
])

LIMITS = {"updated": NOW - 40, "rate_limits": {
    "five_hour": {"used_percentage": 62, "resets_at": NOW + 1 * H + 50 * M},
    "seven_day": {"used_percentage": 38, "resets_at": NOW + 3 * 86400 + 4 * H},
}}

# Paths as ~/projects/<project>, which the cron view groups by just like PROJECTS_DIR/<project>.
CRON_JOBS = [  # (description, schedule, command, last run: seconds ago or None)
    ("Nightly batch: work through the open issues", "30 1 * * *",
     "cd ~/projects/acme-shop && python3 scripts/batch.py --max-issues 8 >> /var/log/batch-acme-shop.log 2>&1", 16 * H + 7 * M),
    ("Back up the orders database", "15 3 * * *",
     "~/projects/acme-shop/scripts/backup.sh >> /var/log/acme-backup.log 2>&1", 14 * H + 22 * M),
    ("Refresh the city list from GeoNames", "0 4 * * 1",
     "cd ~/projects/weather-api && python3 scripts/refresh_cities.py >> /var/log/weather-cities.log 2>&1", 5 * 86400 + 13 * H),
    ("Weekly notes export to S3", "#[off] 0 6 * * 0", "cd ~/projects/notes-app && npm run export:s3", None),
]
CRONTAB = """\
# DO NOT EDIT THIS FILE - edit the master and reinstall.
# (/tmp/crontab.Xq3BvW installed on Mon Oct  5 09:12:44 2026)
# (Cron version -- $Id: crontab.c,v 2.13 1994/01/17 03:20:37 vixie Exp $)

""" + "".join(f"# {d}\n{when} {cmd}\n" for d, when, cmd, _ in CRON_JOBS)
LOGS = {  # /var/log/<name>: the tail the cron view shows
    "batch-acme-shop.log": "== Batch: 6 issues, model sonnet ==\n[01:30:04] #38 Add sitemap.xml  [new: sonnet: small]\n"
                           "[01:51:40] #38 -> merged in 22 min: PR #116, 2 files\n[03:02:11] Done: #38, #39, #40.\n",
    "acme-backup.log": "orders: 48213 rows, dump 61 MB, uploaded to s3://acme-backups/2026-10-17.sql.gz\n",
    "weather-cities.log": "fetched cities15000.zip (2.9 MB), 26473 cities, 112 new, 9 removed\n",
}

# The image the notes-app session "shared": a phone-sized mock of the toolbar it built.
SHARED_PAGE = """<!doctype html><meta charset=utf-8><style>
body{margin:0;font:16px system-ui,sans-serif;background:#fafaf7;color:#222}
.bar{display:flex;gap:6px;padding:10px 12px;border-bottom:1px solid #e3e3dc;background:#fff}
.bar b{display:grid;place-items:center;width:44px;height:44px;border-radius:10px;background:#f1f1ec;font-weight:600}
.bar b.on{background:#2f6fde;color:#fff}.bar b:last-child{margin-left:auto}
h1{font-size:22px;margin:22px 18px 8px}p{margin:0 18px 12px;line-height:1.5;color:#444}
li{margin:0 0 6px}ul{margin:0 18px;color:#444}
</style><div class=bar><b class=on>B</b><b><i>I</i></b><b>•≡</b><b>1.</b><b>⋯</b></div>
<h1>Trip packing list</h1><p>Leaving Friday, back Monday. Weather says rain on Saturday.</p>
<ul><li>Rain jacket</li><li>Charger + cable</li><li>Book for the train</li><li>Hiking boots</li></ul>"""


# ---------------------------------------------------------------- staging

def stage(tmp):
    root, bin_dir = tmp / "projects", tmp / "bin"
    bin_dir.mkdir(parents=True)
    # Stand-ins: print the canned screen, then wait. The dashboard finds them by name in the
    # process tree, as it does the real ones ("claude", ".../batch.py").
    (bin_dir / "claude").write_text('#!/bin/sh\ncat "$1"\nwhile :; do sleep 3600; done\n')
    (bin_dir / "claude").chmod(0o755)
    for p in ("acme-shop", "weather-api", "notes-app"):
        d = root / p
        (d / "scripts").mkdir(parents=True)
        (d / "scripts" / "batch.py").write_text(
            "import sys, time\nsys.stdout.write(open(sys.argv[-1]).read())\nsys.stdout.flush()\ntime.sleep(10**6)\n")
        subprocess.run(["git", "init", "-q", str(d)], check=True)
        subprocess.run(["git", "-C", str(d), "remote", "add", "origin", f"git@github.com:example/{p}.git"], check=True)

    sock = tmp / "tmux.sock"
    tmux = lambda *a: subprocess.run(["tmux", "-u", "-f", "/dev/null", "-S", str(sock), *a], check=True)
    for name, s in SESSIONS.items():
        screen = tmp / f"{name}.txt"
        screen.write_text("\n".join(s["lines"]) + "\n")
        cwd = root / s["project"]
        if s["kind"] == "claude":
            cmd = f"{bin_dir}/claude {screen}; exit"  # "; exit" keeps sh as the pane process
        elif s["running"]:
            cmd = f"python3 {cwd}/scripts/batch.py {s['args']} {screen}; exit"
        else:  # finished batch: the log stays in the pane
            cmd = f"cat {screen}; while :; do sleep 3600; done"
        tmux("new-session", "-d", "-s", name, "-x", "140", "-y", "45", "-c", str(cwd), "sh", "-c", cmd)
    tmux("set-option", "-g", "status", "off")

    data, shots_dir, claude_dir, cron_dir = tmp / "data", tmp / "shots", tmp / "claude", tmp / "cron"
    for d in (data, shots_dir / "shared", claude_dir / "skills", claude_dir / "usage", cron_dir):
        d.mkdir(parents=True)
    (data / "state.json").write_text(json.dumps({"hidden": [], "color": "#30a46c"}))
    (claude_dir / "usage" / "limits.json").write_text(json.dumps(LIMITS))
    (cron_dir / "root").write_text(CRONTAB)
    log = tmp / "log"
    log.mkdir()
    for name, text in LOGS.items():
        (log / name).write_text(text)
        ago = next(j[3] for j in CRON_JOBS if name in j[2]) - 20 * M  # written as the run ended
        os.utime(log / name, (NOW - ago, NOW - ago))
    (log / "syslog").write_text("".join(  # what cron logs when it starts a job; the view reads "last run" from it
        f"{dt.datetime.fromtimestamp(NOW - ago):%b %e %H:%M:%S} demo CRON[{4100 + i}]: (root) CMD ({cmd})\n"
        for i, (_, _, cmd, ago) in enumerate(sorted(CRON_JOBS, key=lambda j: -(j[3] or 0))) if ago))
    os.environ.update(
        STATE_DIR=str(data), PROJECTS_ROOT=str(root), TMUX_SOCKET=str(sock), SHOTS_DIR=str(shots_dir),
        USER_SKILLS=str(claude_dir / "skills"), LIMITS_FILE=str(claude_dir / "usage" / "limits.json"),
        CRON_DIR=str(cron_dir), CRON_USER="root", HOST_LOG_DIR=str(tmp / "log"), BASE_PATH="/ai",
    )
    time.sleep(1)  # let the panes print
    return sock, shots_dir


def serve():
    sys.path.insert(0, str(REPO))
    import app
    import auth
    from http.server import ThreadingHTTPServer

    auth.needs_setup = lambda: False
    auth.valid_token = lambda tok: True
    real = app.collect

    def collect():  # shift the ages; everything else is what app.py read from tmux
        out = real()
        for s in out:
            cfg = SESSIONS.get(s["name"])
            if cfg:
                s["created"], s["activity"] = NOW - cfg["created"], NOW - cfg["activity"]
                if s["running_for"] is not None:
                    s["running_for"] = cfg["running"]
        return out

    app.collect = collect
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)  # any free port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def share_shot(browser, shots_dir, root):
    """What share.sh does, for the notes-app session: image + sidecar in shared/."""
    pg = browser.new_page(viewport={"width": 390, "height": 520}, device_scale_factor=2)
    pg.set_content(SHARED_PAGE)
    ident = dt.datetime.fromtimestamp(NOW - 27 * M).strftime("%Y%m%d-%H%M%S") + "-a1b2c3"
    pg.screenshot(path=str(shots_dir / "shared" / f"{ident}.png"))
    pg.close()
    meta = {"id": ident, "session": "notes-app", "session_created": NOW - SESSIONS["notes-app"]["created"],
            "cwd": str(root / "notes-app"), "caption": "Editor toolbar at 390 px", "created": NOW - 27 * M}
    (shots_dir / "shared" / f"{ident}.json").write_text(json.dumps(meta, indent=2) + "\n")


def main():
    from playwright.sync_api import sync_playwright

    tmp = Path(tempfile.mkdtemp(prefix="aidash-shots-"))
    sock = None
    try:
        sock, shots_dir = stage(tmp)
        srv = serve()
        url = f"http://127.0.0.1:{srv.server_port}/ai/"
        with sync_playwright() as p:
            b = p.chromium.launch()
            share_shot(b, shots_dir, tmp / "projects")

            def page(**opts):
                ctx = b.new_context(color_scheme="light", **opts)
                pg = ctx.new_page()
                pg.goto(url)
                pg.wait_for_selector(".card")
                pg.wait_for_timeout(800)  # usage bars, screenshot thumbnails
                return pg

            desktop = dict(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
            pg = page(**desktop)
            pg.screenshot(path=str(DOCS / "screenshot-desktop.png"), full_page=True)
            print("wrote docs/screenshot-desktop.png")

            pg.locator(".card", has_text="acme-shop-search").get_by_role("button", name="Open").click()
            pg.wait_for_timeout(1200)
            pg.screenshot(path=str(DOCS / "screenshot-terminal.png"))
            print("wrote docs/screenshot-terminal.png")

            pg = page(**desktop)
            pg.get_by_role("button", name="Cron jobs").click()
            pg.wait_for_timeout(1200)
            pg.evaluate("window.scrollTo(0, 0)")
            pg.screenshot(path=str(DOCS / "screenshot-cron.png"))
            print("wrote docs/screenshot-cron.png")

            pg = page(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
            pg.screenshot(path=str(DOCS / "screenshot-mobile.png"))
            print("wrote docs/screenshot-mobile.png")
            b.close()
        srv.shutdown()
    finally:
        if sock:
            subprocess.run(["tmux", "-S", str(sock), "kill-server"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
