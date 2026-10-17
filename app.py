"""AI dashboard: view and drive the host's tmux sessions (Claude sessions and
batch runs), served under BASE_PATH behind a password.

Pure stdlib. Talks to the host tmux server through its socket (mounted into the
container) and reads the host process table through /proc (pid: host). Besides
reading (list-panes, capture-pane) it can type into a session, hide or end one,
and start a new Claude session in a project — all behind the login, and every
write needs the X-AIDash header so another site can't trigger it (CSRF).
Screenshots that sessions share or the user uploads: see shots.py.
"""

import html
import json
import os
import re
import secrets
import shlex
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import auth
import cron
import shots

PORT = int(os.environ.get("PORT", "8000"))
BASE = os.environ.get("BASE_PATH", "/ai").rstrip("/")
TMUX_SOCKET = os.environ.get("TMUX_SOCKET", "/tmp/tmux-0/default")
PROJECTS_ROOT = os.environ.get("PROJECTS_ROOT", "/root/projects")
COOKIE = "aidash"
WEB = Path(__file__).parent / "web"
STATE_FILE = Path(os.environ.get("STATE_DIR", "/data")) / "state.json"
LIMITS_FILE = Path(os.environ.get("LIMITS_FILE", "/root/.claude/usage/limits.json"))
HEX_COLOR = re.compile(r"^#[0-9a-f]{6}$")
SESSION_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
KEYS = {"Enter", "Escape", "C-c", "Up", "Down", "Left", "Right", "Tab", "BTab", "BSpace", "Space",
        "y", "n", *"0123456789"}

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
TS_LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] (.*)")
SPINNER = re.compile(r"^\s*[✻✽✶✳✢·*●]\s+(\S.*?…)\s*(\((.*)\))?\s*$")


# ---------------------------------------------------------------- data

def tmux(*args, stdin=None):
    out = subprocess.run(["tmux", "-u", "-S", TMUX_SOCKET, *args], input=stdin,
                         capture_output=True, text=True, timeout=5)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "tmux failed")
    return out.stdout


def proc_table():
    """pid -> (ppid, starttime_ticks, cmdline) for every host process."""
    procs = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            stat = open(f"/proc/{d}/stat").read()
            rest = stat[stat.rindex(")") + 2:].split()
            cmd = open(f"/proc/{d}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
            procs[int(d)] = (int(rest[1]), int(rest[19]), cmd)
        except (OSError, ValueError, IndexError):
            continue
    return procs


def descendants(pid, procs):
    kids = {}
    for p, (pp, _, _) in procs.items():
        kids.setdefault(pp, []).append(p)
    out, stack = [], list(kids.get(pid, []))
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(kids.get(p, []))
    return out


def boot_time():
    for line in open("/proc/stat"):
        if line.startswith("btime"):
            return int(line.split()[1])
    return 0


def capture(target, lines=60):
    """[(plain, html)] per line; html keeps the terminal colours as inline-styled spans."""
    raw = tmux("capture-pane", "-p", "-e", "-J", "-t", target, "-S", f"-{lines}")
    return ansi_lines(raw)


# Colours tuned for the dashboard's navy terminal background.
PALETTE = ["#4d5a6e", "#ff6b7d", "#45c65a", "#ffc56b", "#5aa9ff", "#d38cff", "#3fd0d8", "#d8edff",
           "#7c8aa0", "#ff9aa6", "#7fd69a", "#ffe08a", "#8ec5ff", "#e5b3ff", "#7ee7ec", "#ffffff"]
SGR = re.compile(r"\x1b\[([0-9;:]*)m")


def xterm256(n):
    if n < 16:
        return PALETTE[n]
    if n < 232:
        n -= 16
        lv = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (lv[n // 36], lv[n // 6 % 6], lv[n % 6])
    g = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (g, g, g)


def sgr_apply(st, params):
    ps = [int(x) if x.isdigit() else 0 for x in params.replace(":", ";").split(";")] if params else [0]
    i = 0
    while i < len(ps):
        c = ps[i]
        if c == 0:
            st.clear()
        elif c in (1, 2, 3, 4, 7, 9):
            st[c] = True
        elif c == 22:
            st.pop(1, None); st.pop(2, None)
        elif c in (23, 24, 27, 29):
            st.pop(c - 20, None)
        elif 30 <= c <= 37 or 90 <= c <= 97:
            st["fg"] = PALETTE[c - 30 if c < 90 else c - 82]
        elif 40 <= c <= 47 or 100 <= c <= 107:
            st["bg"] = PALETTE[c - 40 if c < 100 else c - 92]
        elif c in (39, 49):
            st.pop("fg" if c == 39 else "bg", None)
        elif c in (38, 48) and i + 1 < len(ps):
            key = "fg" if c == 38 else "bg"
            if ps[i + 1] == 5 and i + 2 < len(ps):
                st[key] = xterm256(ps[i + 2]); i += 2
            elif ps[i + 1] == 2 and i + 4 < len(ps):
                st[key] = "#%02x%02x%02x" % tuple(min(v, 255) for v in ps[i + 2:i + 5]); i += 4
        i += 1


def sgr_css(st):
    fg, bg = st.get("fg"), st.get("bg")
    if st.get(7):
        fg, bg = bg or "var(--term)", fg or "var(--term-text)"
    css = []
    if fg: css.append(f"color:{fg}")
    if bg: css.append(f"background:{bg}")
    if st.get(1): css.append("font-weight:700")
    if st.get(2): css.append("opacity:.6")
    if st.get(3): css.append("font-style:italic")
    deco = " ".join(d for k, d in ((4, "underline"), (9, "line-through")) if st.get(k))
    if deco: css.append(f"text-decoration:{deco}")
    return ";".join(css)


def ansi_lines(raw):
    st, out = {}, []
    for line in raw.splitlines():
        line = re.sub(r"\x1b\][^\x07\x1b]*(\x07|\x1b\\)", "", line)  # OSC (hyperlinks, titles)
        plain, parts, pos = [], [], 0
        for m in list(SGR.finditer(line)) + [None]:
            end = m.start() if m else len(line)
            text = ANSI.sub("", line[pos:end])
            if text:
                plain.append(text)
                css = sgr_css(st)
                t = html.escape(text, quote=False)
                parts.append(f'<span style="{css}">{t}</span>' if css else t)
            if m:
                sgr_apply(st, m.group(1))
                pos = m.end()
        p = "".join(plain).rstrip()
        if len(p) >= 20 and not p.strip("─━ "):  # full-width separator: draw a rule that fits any width
            out.append((p, '<span class="rule"></span>'))
            continue
        out.append((p, "".join(parts) if p else ""))
    return out


CHROME = re.compile(r"^[\s─━│┃╭╮╰╯]*$|⏵⏵|shift\+tab|/clear to save|^\s*❯\s*$|← for agents|↓ to manage")


def meaningful(cap, n):
    keep = [h for p, h in cap if p.strip() and not CHROME.search(p)]
    return keep[-n:]


def family(project):
    name = project.lower()
    for suffix in ("-dev", "_dev", "-demo", "_demo"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return re.split(r"[_]", name)[0] or project


def project_of(path):
    if path.startswith(PROJECTS_ROOT + "/"):
        return path[len(PROJECTS_ROOT) + 1:].split("/")[0]
    return path.rstrip("/").split("/")[-1] or "/"


def parse_batch(lines):
    """Progress from the batch.py log format ([HH:MM:SS] #N title / #N -> merged ...)."""
    info = {"planned": None, "merged": [], "failed": [], "current": None,
            "events": [], "limit": None, "sleeping": None, "done": False}
    started = False
    for l in lines:
        m = re.search(r"== Batch: (\d+) issue", l)
        if m:
            info.update(planned=int(m.group(1)), merged=[], failed=[], current=None,
                        events=[], limit=None, sleeping=None, done=False)
            started = True
            continue
        m = TS_LINE.match(l)
        if not m:
            continue
        ts, msg = m.groups()
        if msg.startswith(("PICK ", "skip ")) and not started:
            continue
        r = re.match(r"#(\d+) -> (\w+)(?: in ([\d]+ min))?:?\s*(.*)", msg)
        if r:
            num, outcome, dur, rest = r.groups()
            ev = {"t": ts, "issue": int(num), "outcome": outcome, "dur": dur, "text": rest[:220]}
            info["events"].append(ev)
            if outcome == "merged":
                info["merged"].append(int(num))
                info["current"] = None
            elif outcome == "limit":
                info["limit"] = rest[:160]
            else:
                info["failed"].append(int(num))
                info["current"] = None
            continue
        r = re.match(r"#(\d+) (.+?)\s+\[(.*?)(?:\]|$)", msg)
        if r:
            info["current"] = {"issue": int(r.group(1)), "title": r.group(2), "how": r.group(3), "t": ts}
            info["limit"] = info["sleeping"] = None
            continue
        r = re.search(r"sleeping (\d+) min", msg)
        if r:
            info["sleeping"] = {"min": int(r.group(1)), "t": ts}
            continue
        if msg.startswith("Done:") or "stopping" in msg:
            info["done"] = True
            info["events"].append({"t": ts, "issue": None, "outcome": "info", "dur": None, "text": msg[:220]})
    info["events"] = info["events"][-8:]
    return info


def classify(kind, lines, batch_alive, batch):
    tail = "\n".join([l for l in lines if l.strip()][-25:])
    if kind == "claude":
        if re.search(r"Do you want to|❯ 1\.|Would you like to proceed|Enter to confirm", tail):
            return "attention", "Needs your input"
        if "esc to interrupt" in tail or any(SPINNER.match(l) and "·" in l for l in lines[-8:]):
            return "working", "Working"
        return "idle", "Waiting for you"
    if kind == "batch":
        if batch_alive:
            if batch and batch.get("sleeping"):
                return "waiting", f"Sleeping {batch['sleeping']['min']} min (usage limit)"
            return "working", "Batch running"
        if batch and batch.get("limit"):
            return "limit", "Stopped: usage limit"
        if batch and (batch.get("done") or batch.get("events")):
            return "done", "Finished"
        if re.search(r"Batch finished|^done$", tail, re.M):
            return "done", "Finished"
        return "idle", "Idle shell"
    return ("working", "Running") if batch_alive else ("idle", "Idle shell")


def collect():
    fmt = "\t".join("#{%s}" % f for f in (
        "session_name", "session_created", "session_attached", "window_index", "pane_index",
        "pane_active", "window_active", "pane_pid", "pane_current_command",
        "pane_current_path", "pane_dead", "window_activity", "session_windows"))
    rows = [l.split("\t") for l in tmux("list-panes", "-a", "-F", fmt).splitlines() if l]
    procs = proc_table()
    hz = os.sysconf("SC_CLK_TCK")
    btime = boot_time()
    sessions = {}
    for r in rows:
        (name, created, attached, widx, pidx, pactive, wactive, ppid, cmd, path, dead,
         activity, nwin) = r
        s = sessions.setdefault(name, {"name": name, "created": int(created),
                                       "attached": attached != "0", "windows": int(nwin), "panes": []})
        s["panes"].append({"target": f"{name}:{widx}.{pidx}", "pid": int(ppid), "cmd": cmd,
                           "path": path, "dead": dead == "1", "activity": int(activity),
                           "active": pactive == "1" and wactive == "1"})
    out = []
    for s in sessions.values():
        pane = next((p for p in s["panes"] if p["active"]), s["panes"][0])
        desc = descendants(pane["pid"], procs)
        cmds = [(p, procs[p][2]) for p in desc if p in procs]
        batch_proc = next(((p, c) for p, c in cmds if "batch.py" in c and "--dry-run" not in c), None)
        has_claude = pane["cmd"] == "claude" or any(re.search(r"(^|/)claude(\s|$)", c) for _, c in cmds)
        if s["name"].startswith("batch") or batch_proc:
            kind = "batch"
        elif has_claude:
            kind = "claude"
        else:
            kind = "shell"

        cap = capture(pane["target"], 3000 if kind == "batch" else 400)
        lines = [p for p, _ in cap]
        batch = parse_batch(lines) if kind == "batch" else None
        if kind == "batch" and batch and batch["planned"] is None and not batch["events"] and not batch["current"]:
            batch = None
        if batch and batch["planned"] is None and batch_proc:
            m = re.search(r"--max-issues[ =](\d+)", batch_proc[1])
            batch["planned"] = int(m.group(1)) if m else None
        state, label = classify(kind, lines, bool(batch_proc), batch)

        path = pane["path"]
        if batch_proc:
            try:
                path = os.readlink(f"/proc/{batch_proc[0]}/cwd")
            except OSError:
                pass
        project = project_of(path)

        running_for = None
        if batch_proc:
            running_for = int(time.time() - (btime + procs[batch_proc[0]][1] / hz))

        task = None
        if kind == "claude":
            for l in reversed(lines):
                m = SPINNER.match(l)
                if m and state == "working":
                    task = m.group(1) + (f" ({m.group(3)})" if m.group(3) else "")
                    break
            # Skip the input box at the bottom (text there is a draft, not a sent prompt).
            borders = [i for i, l in enumerate(lines) if re.match(r"^─{20,}", l.strip())]
            history = lines[: borders[-2]] if len(borders) >= 2 else lines
            prompts = [m.group(1).strip() for m in (re.match(r"^❯\s+(.+)", l.strip()) for l in history)
                       if m and not re.match(r"\d\.", m.group(1))]
            task = {"spinner": task, "prompt": prompts[-1][:240] if prompts else None}

        out.append({
            "name": s["name"], "kind": kind, "state": state, "label": label,
            "project": project, "family": family(project), "path": path,
            "created": s["created"], "attached": s["attached"], "windows": s["windows"],
            "activity": pane["activity"], "command": pane["cmd"],
            "batch_cmd": batch_proc[1][:300] if batch_proc else None,
            "running_for": running_for, "batch": batch, "task": task,
            "preview": meaningful(cap, 14),
            "issues_url": issues_url(path),
            "skills": skills_for(path) if kind == "claude" else None,
        })
    hidden = hidden_keys(out)
    for x in out:
        x["hidden"] = (x["name"], x["created"]) in hidden
    order = {"attention": 0, "working": 1, "waiting": 2, "limit": 3, "idle": 4, "done": 5}
    out.sort(key=lambda x: (x["family"], order.get(x["state"], 9), x["name"]))
    return out


def read_limits():
    """Usage limits as last saved by the Claude Code status line (~/.claude/statusline.py)."""
    try:
        return json.loads(LIMITS_FILE.read_text())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- skills & git

USER_SKILLS = os.environ.get("USER_SKILLS", "/root/.claude/skills")


def up_dirs(path):
    """path and its parents, stopping at PROJECTS_ROOT (that's where Claude looks for .claude/)."""
    p = Path(path)
    out = []
    while str(p).startswith(PROJECTS_ROOT + "/"):
        out.append(p)
        p = p.parent
    return out


def frontmatter(text):
    meta, body = {}, text
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---")
        for line in head.splitlines():
            k, sep, v = line.partition(":")
            if sep and not line.startswith((" ", "\t")):
                meta[k.strip()] = v.strip().strip('"\'')
    first = next((l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")), "")
    return meta, first


def read_skills(root):
    """Skills (<root>/<name>/SKILL.md) and plain commands (<root>/<name>.md)."""
    found = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return found
    for e in entries:
        f = Path(root, e, "SKILL.md") if Path(root, e).is_dir() else Path(root, e)
        if not f.is_file() or f.suffix != ".md":
            continue
        try:
            meta, first = frontmatter(f.read_text(errors="replace")[:4000])
        except OSError:
            continue
        name = meta.get("name") or (e if f.name == "SKILL.md" else e[:-3])
        if not re.match(r"^[\w:.-]+$", name):
            continue
        found.append({"name": name, "desc": (meta.get("description") or first)[:200],
                      "hint": meta.get("argument-hint", "")[:80]})
    return found


SKILL_CACHE = {}  # path -> (time, skills)


def skills_for(path):
    hit = SKILL_CACHE.get(path)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    seen, project = set(), []
    for d in up_dirs(path):
        for kind in ("skills", "commands"):
            for sk in read_skills(d / ".claude" / kind):
                if sk["name"] not in seen:
                    seen.add(sk["name"])
                    project.append(sk)
    user = [sk for sk in read_skills(USER_SKILLS) if sk["name"] not in seen]
    res = {"project": project, "user": user}
    SKILL_CACHE[path] = (time.time(), res)
    return res


def git_config(d):
    g = d / ".git"
    if g.is_file():  # worktree / submodule: "gitdir: <path>", config lives in the common dir
        gd = Path(g.read_text().split("gitdir:", 1)[-1].strip())
        gd = gd if gd.is_absolute() else (d / gd).resolve()
        common = gd / "commondir"
        if common.is_file():
            gd = (gd / common.read_text().strip()).resolve()
        return gd / "config"
    return g / "config"


def issues_url(path):
    for d in up_dirs(path):
        if not (d / ".git").exists():
            continue
        try:
            cfg = git_config(d).read_text()
        except OSError:
            return None
        m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', cfg)
        if not m:
            return None
        url = m.group(1)
        m = (re.match(r"^(?:ssh://)?[^@/]+@([^:/]+)[:/](?:\d+/)?(.+?)(?:\.git)?/?$", url)
             or re.match(r"^https?://(?:[^@/]+@)?([^/]+)/(.+?)(?:\.git)?/?$", url))
        if not m:
            return None
        host, repo = m.groups()
        # SSH aliases like "github-the-drift" / "github.com-deskdonkey-app" are github.com.
        host = "github.com" if "github" in host else host
        return f"https://{host}/{repo}/issues"
    return None


# ---------------------------------------------------------------- state & actions

STATE_LOCK = threading.Lock()


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"hidden": []}


def save_state(st):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE_FILE)


def hidden_keys(sessions):
    """Hidden = (name, created), so a new session that reuses a name shows up again."""
    with STATE_LOCK:
        st = load_state()
        live = {(s["name"], s["created"]) for s in sessions}
        keep = [h for h in st["hidden"] if (h["name"], h["created"]) in live]
        if keep != st["hidden"]:
            st["hidden"] = keep
            save_state(st)
        return {(h["name"], h["created"]) for h in keep}


def set_hidden(name, created, hide):
    with STATE_LOCK:
        st = load_state()
        st["hidden"] = [h for h in st["hidden"] if (h["name"], h["created"]) != (name, created)]
        if hide:
            st["hidden"].append({"name": name, "created": created})
        save_state(st)


def get_color():
    with STATE_LOCK:
        return load_state().get("color", "")


def set_color(color):
    """Server color for the header dot and favicon, so tabs from different servers tell apart."""
    color = color.strip().lower()
    if color and not HEX_COLOR.match(color):
        raise ValueError("color must be #rrggbb")
    with STATE_LOCK:
        st = load_state()
        st["color"] = color
        save_state(st)


def session_names():
    return tmux("list-sessions", "-F", "#{session_name}").split()


def send_text(name, text, enter=True):
    target = f"={name}:"
    if "\n" in text:
        # Bracketed paste, so Claude takes a multi-line message as one prompt.
        tmux("load-buffer", "-b", "aidash", "-", stdin=text)
        tmux("paste-buffer", "-b", "aidash", "-d", "-p", "-t", target)
    elif text:
        tmux("send-keys", "-t", target, "-l", "--", text)
    if enter:
        time.sleep(0.15)  # let the TUI take the text before Enter submits it
        tmux("send-keys", "-t", target, "Enter")


def projects():
    out = []
    for d in sorted(Path(PROJECTS_ROOT).iterdir(), key=lambda p: p.name.lower()):
        if d.is_dir() and not d.name.startswith("."):
            out.append({"name": d.name, "git": (d / ".git").exists()})
    return out


def new_session(project, name, resume, prompt):
    if not any(p["name"] == project for p in projects()):
        raise ValueError("unknown project")
    if not SESSION_NAME.match(name):
        raise ValueError("name: letters, digits, - and _ only (max 40)")
    if name in session_names():
        raise ValueError(f"a session named {name} already exists")
    # "--" so a prompt that starts with "-" can't pass as a flag (--dangerously-skip-permissions).
    cmd = "claude" + (" --continue" if resume else "") + (" -- " + shlex.quote(prompt) if prompt else "")
    # Drop to a shell when Claude exits, so the session (and its scrollback) stays around.
    tmux("new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", f"{PROJECTS_ROOT}/{project}",
         f'PATH="$HOME/.local/bin:$PATH" {cmd}; exec bash')


def shot_list():
    out = []
    for s in shots.listing():
        project = project_of(s["cwd"]) if s["cwd"] else ""
        out.append({**s, "project": project, "family": family(project) if project else ""})
    return out


def upload_shot(handler):
    """Raw image body; ?s=<session> ties it to a session (its folder becomes the project)."""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    name, caption = (q.get("s") or [""])[0], (q.get("caption") or [""])[0]
    created, cwd = 0, ""
    if name:
        if name not in session_names():
            raise ValueError("no such session")
        created, _, cwd = tmux("display-message", "-p", "-t", f"={name}:",
                               "#{session_created}\t#{pane_current_path}").strip().partition("\t")
    return shots.save_upload(handler.body(shots.MAX_UPLOAD), name, int(created or 0), cwd, caption)


def run_cron_job(body):
    """Start a crontab job now, in a tmux session, with cron's environment.
    Output that the job redirects to a log is followed live in the pane."""
    listing = cron.listing()
    jobs = listing["jobs"]
    try:
        job = jobs[int(body.get("id"))]
    except (TypeError, ValueError, IndexError):
        raise ValueError("no such job")
    if body.get("raw") != job["raw"]:
        raise cron.Conflict("that job changed since you loaded it; reload and try again")
    base = "cron-" + re.sub(r"[^A-Za-z0-9_-]+", "-", job["project"] or "job")[:30]
    taken, name, i = set(session_names()), base, 2
    while name in taken:
        name, i = f"{base}-{i}", i + 1
    run = cron.run_command(job, listing["env"])
    log = job["log"]
    if log:  # the job writes to a log: show its new lines while it runs
        run = (f"tail -n 0 -F {shlex.quote(log)} 2>/dev/null & T=$!; {run}; rc=$?; sleep 1; kill $T; "
               f"echo; echo \"[exit $rc] full log: {log}\"")
    else:
        run += '; echo; echo "[exit $?]"'
    tmux("new-session", "-d", "-s", name, "-x", "200", "-y", "50", f"{run}; exec bash")
    return name


# ---------------------------------------------------------------- http

LOGIN_HTML = (WEB / "login.html").read_text()
DASH_HTML = (WEB / "index.html").read_text()
SETUP_HTML = (WEB / "setup.html").read_text()


CSP = ("default-src 'none'; script-src {script}; style-src 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


class Handler(BaseHTTPRequestHandler):
    server_version = "aidash"
    timeout = 30  # a client that stalls (slowloris, bogus Content-Length) loses its thread

    def log_message(self, fmt, *args):
        pass

    def ip(self):
        peer = self.client_address[0]
        if peer in auth.proxy_ips():
            return self.headers.get("X-Real-IP") or peer
        return peer

    def cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and v:
                return v
        return None

    def authed(self):
        return auth.valid_token(self.cookie_token())

    def body(self, limit):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        return self.rfile.read(max(0, min(n, limit))) if n > 0 else b""

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=(), nonce=None, cache="no-store"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP.format(script=f"'nonce-{nonce}'" if nonce else "'none'"))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, to, headers=()):
        self.send(303, "", headers=[("Location", to), *headers])

    def route(self):
        path = urllib.parse.urlsplit(self.path).path
        if not path.startswith(BASE):
            return None
        return path[len(BASE):] or "/"

    def login_page(self, error=""):
        msg = f'<div class="err">{html.escape(error)}</div>' if error else ""
        self.send(200 if not error else 401, LOGIN_HTML.replace("{{BASE}}", BASE).replace("{{ERROR}}", msg))

    def session_cookie(self, ip):
        return ("Set-Cookie", f"{COOKIE}={auth.make_token(ip)}; Path={BASE}/; Max-Age={auth.SESSION_TTL}; "
                              "HttpOnly; Secure; SameSite=Strict")

    def setup_page(self, error="", pending=None, code=200):
        """Step 1: new password (or the existing one, if only the TOTP is missing). Step 2: scan the QR, enter a code."""
        msg = f'<div class="err">{html.escape(error)}</div>' if error else ""
        if pending:
            pid, secret = pending
            qr = auth.qr_svg(auth.otpauth_uri(secret))
            grouped = " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))
            body = f"""<p class="step">Step 2 of 2 · Two-factor login</p>
      <p class="hint">Scan this with an authenticator app (Google Authenticator, 1Password, Aegis, …),
        then enter the 6-digit code it shows.</p>
      {f'<div class="qr">{qr}</div>' if qr else ""}
      <p class="hint">{"Or type" if qr else "Type"} this key into the app:</p>
      <code class="secret">{grouped}</code>
      <input type="hidden" name="step" value="confirm">
      <input type="hidden" name="pending" value="{html.escape(pid)}">
      <label for="code">Code from the app</label>
      <input id="code" name="code" inputmode="numeric" pattern="[0-9 ]{{6,7}}" autocomplete="one-time-code" autofocus required>
      <button type="submit">Finish setup</button>"""
        else:
            if auth.CREDS["password"]:  # only the TOTP is missing: prove you know the password
                fields = """
      <p class="hint">A password is set, but no two-factor login yet. Enter the password to add it.</p>
      <label for="pw">Password</label>
      <input id="pw" name="password" type="password" autocomplete="current-password" autofocus required>"""
            else:
                fields = f"""
      <p class="hint">This dashboard has no login yet. Choose a password; next you add an authenticator app.</p>
      <label for="pw">New password <small>(at least {auth.MIN_PASSWORD} characters)</small></label>
      <input id="pw" name="password" type="password" minlength="{auth.MIN_PASSWORD}" autocomplete="new-password" autofocus required>
      <label for="pw2">Repeat the password</label>
      <input id="pw2" name="password2" type="password" minlength="{auth.MIN_PASSWORD}" autocomplete="new-password" required>"""
            steps = 2 if not auth.CREDS["totp"] else 1
            body = f"""<p class="step">Step 1 of {steps} · {"Password" if not auth.CREDS["password"] else "Confirm"}</p>{fields}
      <input type="hidden" name="step" value="start">
      <button type="submit">Continue</button>"""
        page = SETUP_HTML.replace("{{BASE}}", BASE).replace("{{ERROR}}", msg).replace("{{BODY}}", body)
        self.send(code, page)

    def do_GET(self):
        r = self.route()
        if r is None:
            return self.send(404, "not found", "text/plain")
        if r == "/health":
            return self.send(200, "ok", "text/plain")
        if r == "/logout":
            tok = self.cookie_token()
            if tok:
                auth.drop_token(tok)
            return self.redirect(f"{BASE}/", [("Set-Cookie", f"{COOKIE}=; Path={BASE}/; Max-Age=0; HttpOnly; Secure; SameSite=Strict")])
        if auth.needs_setup():
            if r.startswith("/api/"):
                return self.send(401, '{"error":"setup"}', "application/json")
            return self.setup_page()
        if not self.authed():
            if r.startswith("/api/"):
                return self.send(401, '{"error":"login"}', "application/json")
            return self.login_page()
        if r == "/":
            nonce = secrets.token_urlsafe(16)
            page = DASH_HTML.replace("{{BASE}}", BASE).replace("{{COLOR}}", get_color()).replace("<script>", f'<script nonce="{nonce}">', 1)
            return self.send(200, page, nonce=nonce)
        if r == "/api/sessions":
            try:
                data = {"ok": True, "now": int(time.time()), "sessions": collect(), "limits": read_limits()}
            except Exception as e:  # tmux server gone, socket missing, ...
                data = {"ok": False, "now": int(time.time()), "error": str(e), "sessions": [], "limits": read_limits()}
            data["shots_rev"] = shots.rev()
            return self.send(200, json.dumps(data), "application/json")
        if r.startswith("/api/cron"):
            return self.cron_get(r)
        if r == "/api/shots":
            return self.send(200, json.dumps({"shots": shot_list(), **shots.info()}), "application/json")
        if r.startswith("/shots/"):  # /shots/<shared|uploads>/<id>.<ext>
            _, src, name = (r[1:].split("/") + ["", ""])[:3]
            hit = shots.file_for(src, name)
            if not hit:
                return self.send(404, "not found", "text/plain")
            # ids are never reused, so the browser may keep the image (but no shared cache may)
            return self.send(200, hit[0].read_bytes(), hit[1], cache="private, max-age=604800, immutable")
        if r == "/api/projects":
            return self.send(200, json.dumps({"projects": projects()}), "application/json")
        if r == "/api/pane":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            name = (q.get("s") or [""])[0]
            try:
                if name not in session_names():
                    return self.send(404, '{"error":"no such session"}', "application/json")
                cap = capture(f"={name}:", 400)
                while cap and not cap[-1][0].strip():
                    cap.pop()
                # Claude pins its input box to the bottom of a tall pane, so a near-empty
                # session is mostly blank rows; keep at most one blank line in a row.
                cap = [c for i, c in enumerate(cap)
                       if c[0].strip() or i == 0 or cap[i - 1][0].strip()]
                return self.send(200, json.dumps({"html": [h for _, h in cap]}), "application/json")
            except Exception as e:
                return self.send(500, json.dumps({"error": str(e)}), "application/json")
        return self.send(404, "not found", "text/plain")

    def do_POST(self):
        r = self.route()
        if r and r.startswith("/api/"):
            return self.api_post(r)
        if r == "/setup":
            return self.setup_post()
        if r != "/login":
            return self.send(404, "not found", "text/plain")
        if auth.needs_setup():
            return self.redirect(f"{BASE}/")
        ip = self.ip()
        if auth.throttled(ip):
            return self.login_page("Too many attempts. Try again in a few minutes.")
        form = urllib.parse.parse_qs(self.body(4096).decode(errors="replace"))
        pw = (form.get("password") or [""])[0]
        code = (form.get("code") or [""])[0]
        if auth.password_ok(pw) and auth.totp_ok(code):
            auth.clear_fails(ip)
            return self.redirect(f"{BASE}/", [self.session_cookie(ip)])
        auth.record_fail(ip)
        time.sleep(1)
        return self.login_page("Wrong password or code.")

    def setup_post(self):
        if not auth.needs_setup():
            return self.redirect(f"{BASE}/")
        ip = self.ip()
        if auth.throttled(ip):
            return self.setup_page("Too many attempts. Try again in a few minutes.", code=429)
        form = urllib.parse.parse_qs(self.body(4096).decode(errors="replace"))
        arg = lambda k: (form.get(k) or [""])[0]
        if arg("step") == "confirm":
            pid = arg("pending")
            pending = auth.setup_pending(pid)
            try:
                auth.setup_confirm(pid, arg("code"))
            except ValueError as e:
                auth.record_fail(ip)
                return self.setup_page(str(e), (pid, pending["totp"]) if pending else None, 400)
            return self.redirect(f"{BASE}/", [self.session_cookie(ip)])
        try:
            pending = auth.setup_start(arg("password"), arg("password2"))
        except PermissionError as e:
            auth.record_fail(ip)
            time.sleep(1)
            return self.setup_page(str(e), code=401)
        except ValueError as e:
            return self.setup_page(str(e), code=400)
        if pending:
            return self.setup_page(pending=pending)
        return self.redirect(f"{BASE}/", [self.session_cookie(ip)])

    def cron_get(self, r):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        arg = lambda k: (q.get(k) or [""])[0]
        try:
            if not cron.available():
                return self.send(200, json.dumps({"available": False, "path": str(cron.crontab_path())}), "application/json")
            if r == "/api/cron":
                data = {"available": True, "now": int(time.time()), **cron.listing()}
            elif r == "/api/cron/raw":
                text = cron.read_text()
                data = {"text": text, "rev": cron.rev(text)}
            elif r == "/api/cron/preview":
                sched = " ".join(arg("s").split())
                if sched.lower() == "@reboot":
                    data = {"human": "at boot", "next": []}
                else:
                    data = {"human": cron.describe(sched), "next": cron.next_runs(sched, 5)}
            elif r == "/api/cron/log":
                job = cron.listing()["jobs"][int(arg("id"))]
                if not job["log"]:
                    raise ValueError("this job doesn't write to a log file (>> /var/log/... 2>&1)")
                data = {"path": job["log"], **cron.tail_log(job["log"])}
            else:
                return self.send(404, "not found", "text/plain")
            return self.send(200, json.dumps(data), "application/json")
        except (ValueError, IndexError) as e:
            return self.send(400, json.dumps({"error": str(e) or "bad request"}), "application/json")
        except Exception as e:
            return self.send(500, json.dumps({"error": str(e)}), "application/json")

    def api_post(self, r):
        def reply(code, obj):
            return self.send(code, json.dumps(obj), "application/json")
        if not self.authed():
            return reply(401, {"error": "login"})
        if self.headers.get("X-AIDash") != "1":
            return reply(403, {"error": "missing X-AIDash header"})
        try:
            if r == "/api/shots/upload":
                if int(self.headers.get("Content-Length") or 0) > shots.MAX_UPLOAD:
                    self.close_connection = True
                    return reply(413, {"error": f"image too large (max {shots.MAX_UPLOAD // 1024 // 1024} MB)"})
                return reply(200, {"ok": True, "path": upload_shot(self)})
            body = json.loads(self.body(65536) or b"{}")
            if r == "/api/shots/delete":
                shots.delete(str(body.get("src") or ""), str(body.get("id") or ""))
                return reply(200, {"ok": True})
            if r == "/api/new":
                name = str(body.get("name") or "").strip()
                new_session(str(body.get("project") or ""), name,
                            bool(body.get("resume")), str(body.get("prompt") or "").strip()[:4000])
                return reply(200, {"ok": True, "name": name})
            if r == "/api/color":
                set_color(str(body.get("color") or ""))
                return reply(200, {"ok": True})
            if r.startswith("/api/cron/"):
                if not cron.available():
                    return reply(400, {"error": "the crontab is not mounted or not writable"})
                op = r[len("/api/cron/"):]
                if op == "run":
                    return reply(200, {"ok": True, "name": run_cron_job(body)})
                if op == "raw":
                    return reply(200, {"ok": True, "rev": cron.write(str(body.get("text") or ""), str(body.get("rev") or "x"))})
                return reply(200, {"ok": True, "rev": cron.mutate(op, body)})
            name = str(body.get("s") or "")
            if name not in session_names():
                return reply(404, {"error": "no such session"})
            if r == "/api/send":
                send_text(name, str(body.get("text") or "")[:20000], bool(body.get("enter", True)))
            elif r == "/api/keys":
                keys = body.get("keys") or []
                if not keys or any(k not in KEYS for k in keys):
                    return reply(400, {"error": "key not allowed"})
                tmux("send-keys", "-t", f"={name}:", *keys)
            elif r == "/api/hide":
                set_hidden(name, int(body.get("created") or 0), bool(body.get("hidden", True)))
            elif r == "/api/kill":
                tmux("kill-session", "-t", f"={name}")
            else:
                return reply(404, {"error": "not found"})
            return reply(200, {"ok": True})
        except cron.Conflict as e:
            return reply(409, {"error": str(e)})
        except ValueError as e:
            return reply(400, {"error": str(e)})
        except Exception as e:
            return reply(500, {"error": str(e)})


if __name__ == "__main__":
    auth.startup()
    print(shots.install_skill(os.environ.get("DASHBOARD_URL") or f"https://{os.environ.get('VIRTUAL_HOST', 'localhost')}{BASE}/"),
          flush=True)
    print(f"aidash on :{PORT}{BASE}/", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
