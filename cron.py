"""The host user's crontab as structured jobs: list, create, edit, enable/disable,
delete and run now.

The crontab file lives in the host's cron spool (mounted at CRON_DIR). It is
edited in place, so its owner, group and mode stay what `crontab` set, and the
spool dir's mtime is bumped afterwards, because that is what makes cron re-read
it. Every save first copies the old file to /data/cron-backups.

File model: a job is its line plus the comment lines directly above it (its
description). A disabled job is the job line prefixed with DISABLED, so plain
comments that merely look like a job (the stock header has one) stay comments.
Everything else (blank lines, comments, VAR=value lines) is kept verbatim.
"""

import hashlib
import os
import re
import shlex
import time
from datetime import datetime, timedelta
from pathlib import Path

CRON_DIR = Path(os.environ.get("CRON_DIR", "/cron"))
CRON_USER = os.environ.get("CRON_USER", "root")
PROJECTS_ROOT = os.environ.get("PROJECTS_ROOT", "/root/projects")
LOG_ROOT = os.environ.get("HOST_LOG_DIR", "/hostlog")  # host /var/log, read-only
BACKUPS = Path(os.environ.get("STATE_DIR", "/data")) / "cron-backups"
DISABLED = "#[off] "
MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *",
          "@weekly": "0 0 * * 0", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9,
         "oct": 10, "nov": 11, "dec": 12, "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
FIELDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # minute hour dom month dow (7 = Sunday too)
ENV_LINE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*=")
LOG_REDIRECT = re.compile(r"\s+>>\s*(\S+)\s+2>&1\s*$")


class Conflict(Exception):
    pass


def crontab_path():
    return CRON_DIR / CRON_USER


def available():
    return crontab_path().is_file() and os.access(crontab_path(), os.W_OK)


def read_text():
    return crontab_path().read_text()


def rev(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- schedules

def parse_field(spec, lo, hi):
    out = set()
    for part in spec.lower().split(","):
        rng, _, step = part.partition("/")
        step = int(step) if step else 1
        if step < 1:
            raise ValueError(f"bad step in {spec!r}")
        if rng == "*":
            a, b = lo, hi
        else:
            a_s, dash, b_s = rng.partition("-")
            a = NAMES.get(a_s, None) if not a_s.isdigit() else int(a_s)
            b = (NAMES.get(b_s) if not b_s.isdigit() else int(b_s)) if dash else (hi if step > 1 else a)
            if a is None or b is None:
                raise ValueError(f"cannot read {spec!r}")
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ValueError(f"{spec!r} is out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


def parse_schedule(sched):
    """5 cron fields (or an @macro) -> (minutes, hours, doms, months, dows, dom_star, dow_star)."""
    sched = MACROS.get(sched.strip().lower(), sched.strip())
    parts = sched.split()
    if len(parts) != 5:
        raise ValueError("a schedule has 5 fields: minute hour day-of-month month day-of-week")
    sets = [parse_field(p, lo, hi) for p, (lo, hi) in zip(parts, FIELDS)]
    if 7 in sets[4]:
        sets[4] = (sets[4] - {7}) | {0}
    return (*sets, parts[2].startswith("*"), parts[4].startswith("*"))


def next_runs(sched, n=3, start=None):
    """The next n run times (epoch seconds) in the container's local time (= host TZ)."""
    mins, hours, doms, months, dows, dom_star, dow_star = parse_schedule(sched)
    t = (start or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    out, limit = [], t + timedelta(days=366 * 5)

    def day_ok(d):
        dom_ok, dow_ok = d.day in doms, (d.weekday() + 1) % 7 in dows
        if dom_star or dow_star:  # cron: if either field is *, both must match; else either does
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    while len(out) < n and t < limit:
        if t.month not in months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        if not day_ok(t):
            t = t.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if t.hour not in hours:
            t = t.replace(minute=0) + timedelta(hours=1)
            continue
        if t.minute not in mins:
            t += timedelta(minutes=1)
            continue
        out.append(int(t.timestamp()))
        t += timedelta(minutes=1)
    return out


def describe(sched):
    """A short English reading of the common schedule shapes; falls back to the raw fields."""
    s = MACROS.get(sched.strip().lower(), sched.strip())
    m, h, dom, mon, dow = s.split()
    days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def at():
        hs = [int(x) for x in sorted(parse_field(h, 0, 23))]
        if m.isdigit() and len(hs) <= 4:
            return "at " + ", ".join(f"{x:02d}:{int(m):02d}" for x in hs)
        return None

    if m == "*" and h == "*" and dom == mon == dow == "*":
        return "every minute"
    if m.startswith("*/") and h == "*" and dom == mon == dow == "*":
        return f"every {m[2:]} minutes"
    if m.isdigit() and h == "*" and dom == mon == dow == "*":
        return f"hourly at :{int(m):02d}"
    if m.isdigit() and h.startswith("*/") and dom == mon == dow == "*":
        return f"every {h[2:]} hours at :{int(m):02d}"
    when = at()
    if not when:
        return s
    if dom == mon == dow == "*":
        return f"daily {when}"
    if dom == mon == "*":
        try:
            ds = sorted(parse_field(dow, 0, 7))
        except ValueError:
            return s
        if ds == [1, 2, 3, 4, 5]:
            return f"weekdays {when}"
        return ", ".join(days[d] for d in ds) + f" {when}"
    if mon == dow == "*" and dom.isdigit():
        return f"monthly on day {dom} {when}"
    return s


# ---------------------------------------------------------------- file model

def split_job(line):
    """'m h dom mon dow cmd' / '@daily cmd' -> (schedule, command) or None."""
    s = line.strip()
    if s.startswith("@"):
        parts = s.split(None, 1)
        if parts[0].lower() in MACROS and len(parts) == 2:
            return parts[0], parts[1]
        if parts[0].lower() == "@reboot" and len(parts) == 2:
            return parts[0], parts[1]
        return None
    parts = s.split(None, 5)
    if len(parts) < 6:
        return None
    try:
        parse_schedule(" ".join(parts[:5]))
    except ValueError:
        return None
    return " ".join(parts[:5]), parts[5]


def parse(text):
    """-> (blocks, jobs). blocks: list of str lines or job dicts, in file order."""
    lines = text.splitlines()
    blocks, jobs, pending = [], [], []  # pending = comment lines that may belong to the next job

    def flush():
        blocks.extend(pending)
        pending.clear()

    for line in lines:
        s = line.strip()
        enabled = True
        body = line
        if s.startswith(DISABLED.strip()):
            body, enabled = s[len(DISABLED.strip()):].strip(), False
        job = None if (s.startswith("#") and enabled) or not s or ENV_LINE.match(s) else split_job(body)
        if job:
            sched, cmd = job
            desc = [p.lstrip("#").strip() for p in pending]
            pending.clear()
            j = {"id": len(jobs), "schedule": sched, "command": cmd, "enabled": enabled,
                 "description": "\n".join(desc).strip(), "raw": line}
            jobs.append(j)
            blocks.append(j)
        elif s.startswith("#"):
            pending.append(line)
        else:
            flush()
            blocks.append(line)
    flush()
    return blocks, jobs


def render(blocks):
    out = []
    for b in blocks:
        if isinstance(b, str):
            out.append(b)
            continue
        for d in (b["description"] or "").splitlines():
            out.append(("# " + d).rstrip())
        line = f"{b['schedule']} {b['command']}"
        out.append(line if b["enabled"] else DISABLED + line)
    return "\n".join(out).rstrip("\n") + "\n"


def env_of(blocks):
    env = {}
    for b in blocks:
        if isinstance(b, str) and ENV_LINE.match(b.strip()):
            k, _, v = b.strip().partition("=")
            env[k.strip()] = v.strip().strip('"\'')
    return env


# ---------------------------------------------------------------- run history & logs

def project_of(cmd):
    """The first project folder the command mentions (absolute or ~/ path), if any."""
    root = re.escape(PROJECTS_ROOT.rstrip("/"))
    m = re.search(rf"(?:{root}|~/{re.escape(Path(PROJECTS_ROOT).name)})/([^/\s;&|>]+)", cmd)
    return m.group(1) if m else None


def log_of(cmd):
    m = LOG_REDIRECT.search(cmd)
    return m.group(1) if m else None


def host_log(path):
    """Host path -> path inside the container, only for files under /var/log."""
    p = os.path.normpath(path)
    if not p.startswith("/var/log/"):
        return None
    return Path(LOG_ROOT + p[len("/var/log"):])


SYSLOG_LINE = re.compile(r"^(\w{3}\s+\d+ \d\d:\d\d:\d\d|\S+T\S+) \S+ CRON\[\d+\]: \((\S+)\) CMD \((.*)\)$")
SYSLOG = {"ino": None, "offset": 0, "runs": {}}  # read incrementally: only new bytes after the first scan


def syslog_time(ts, year):
    if "T" in ts:
        return datetime.fromisoformat(ts).timestamp()
    d = datetime.strptime(f"{year} {' '.join(ts.split())}", "%Y %b %d %H:%M:%S")
    if d > datetime.now() + timedelta(days=1):  # December lines read in January
        d = d.replace(year=year - 1)
    return d.timestamp()


def scan_syslog(path, start, runs):
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read()
    end = data.rfind(b"\n") + 1  # leave a half-written last line for the next read
    year = datetime.now().year
    for line in data[:end].decode(errors="replace").splitlines():
        if "CRON[" not in line:
            continue
        m = SYSLOG_LINE.match(line)
        if m and m.group(2) == CRON_USER:
            try:
                runs[m.group(3)] = int(syslog_time(m.group(1), year))
            except ValueError:
                pass
    return start + end


def last_runs():
    """command -> epoch of its latest start, from the host syslog (needs read access to it)."""
    f = Path(LOG_ROOT, "syslog")
    try:
        st = f.stat()
        if st.st_ino != SYSLOG["ino"] or st.st_size < SYSLOG["offset"]:  # first run or rotated
            runs = {}
            try:
                scan_syslog(Path(LOG_ROOT, "syslog.1"), 0, runs)
            except OSError:
                pass
            SYSLOG.update(ino=st.st_ino, offset=0, runs=runs)
        SYSLOG["offset"] = scan_syslog(f, SYSLOG["offset"], SYSLOG["runs"])
    except OSError:
        return {}
    return SYSLOG["runs"]


def tail_log(path, lines=200):
    p = host_log(path)
    if not p:
        raise ValueError("only logs under /var/log can be shown")
    try:
        with p.open("rb") as fh:
            size = fh.seek(0, 2)
            fh.seek(max(0, size - 200_000))
            text = fh.read().decode(errors="replace")
    except FileNotFoundError:
        return {"lines": [], "missing": True}
    return {"lines": text.splitlines()[-lines:], "size": size, "mtime": int(p.stat().st_mtime)}


# ---------------------------------------------------------------- read / write

def listing():
    text = read_text()
    blocks, jobs = parse(text)
    runs = last_runs()
    for j in jobs:
        sched = j["schedule"]
        try:
            j["next"] = [] if sched == "@reboot" or not j["enabled"] else next_runs(sched)
            j["human"] = "at boot" if sched == "@reboot" else describe(sched)
        except ValueError:
            j["next"], j["human"] = [], sched
        j["log"] = log_of(j["command"])
        j["project"] = project_of(j["command"])
        j["last"] = runs.get(j["command"])
        p = host_log(j["log"]) if j["log"] else None
        try:
            j["log_mtime"] = int(p.stat().st_mtime) if p else None
        except OSError:
            j["log_mtime"] = None
    return {"rev": rev(text), "user": CRON_USER, "jobs": jobs, "env": env_of(blocks),
            "tz": time.strftime("%Z"), "syslog": bool(runs)}


def write(text, expect_rev):
    """Replace the crontab, if nobody changed it since expect_rev was read."""
    cur = read_text()
    if expect_rev and rev(cur) != expect_rev:
        raise Conflict("the crontab changed since you loaded it; reload and try again")
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s and not s.startswith("#") and not ENV_LINE.match(s) and not split_job(s):
            raise ValueError(f"line {i} is not a valid cron line: {s[:80]}")
    BACKUPS.mkdir(parents=True, exist_ok=True)
    (BACKUPS / f"{CRON_USER}-{datetime.now():%Y%m%d-%H%M%S-%f}").write_text(cur)
    for old in sorted(BACKUPS.glob(f"{CRON_USER}-*"))[:-50]:
        old.unlink()
    # In place, so owner/group/mode stay as crontab(1) set them; then bump the spool
    # dir's mtime, which is what cron watches to re-read the file.
    with crontab_path().open("r+") as fh:
        fh.write(text)
        fh.truncate()
    os.utime(CRON_DIR)
    return rev(text)


def clean_job(body):
    sched = " ".join(str(body.get("schedule") or "").split())
    cmd = str(body.get("command") or "").strip()
    desc = str(body.get("description") or "").strip()
    if sched.lower() != "@reboot":
        parse_schedule(sched)
    if not cmd or "\n" in cmd or "\r" in cmd:
        raise ValueError("the command must be one non-empty line")
    if re.search(r"(?<!\\)%", cmd):
        raise ValueError("cron treats % as a line break; write \\% for a literal percent sign")
    if len(desc) > 2000:
        raise ValueError("description too long")
    return {"schedule": sched, "command": cmd, "description": desc, "enabled": bool(body.get("enabled", True))}


def mutate(op, body):
    text = read_text()
    if body.get("rev") and rev(text) != body["rev"]:
        raise Conflict("the crontab changed since you loaded it; reload and try again")
    blocks, jobs = parse(text)
    if op == "create":
        job = clean_job(body)
        if blocks and not (isinstance(blocks[-1], str) and not blocks[-1].strip()):
            blocks.append("")
        blocks.append(job)
        return write(render(blocks), None)
    try:
        job = jobs[int(body.get("id"))]
    except (TypeError, ValueError, IndexError):
        raise ValueError("no such job")
    if body.get("raw") != job["raw"]:
        raise Conflict("that job changed since you loaded it; reload and try again")
    if op == "update":
        job.update(clean_job(body))
    elif op == "toggle":
        job["enabled"] = bool(body.get("enabled"))
    elif op == "delete":
        i = blocks.index(job)
        del blocks[i]
        # drop the blank line that separated it, so deletes don't pile up gaps
        if i > 0 and isinstance(blocks[i - 1], str) and not blocks[i - 1].strip() and \
                (i >= len(blocks) or (isinstance(blocks[i], str) and not blocks[i].strip())):
            del blocks[i - 1]
    else:
        raise ValueError("unknown operation")
    return write(render(blocks), None)


def run_command(job, env):
    """The shell line that runs a job like cron does: minimal environment, /bin/sh."""
    cmd = job["command"].replace("\\%", "%")
    home = env.get("HOME", "/root" if CRON_USER == "root" else f"/home/{CRON_USER}")
    base = {"HOME": home, "LOGNAME": CRON_USER, "SHELL": "/bin/sh", "PATH": "/usr/bin:/bin", **env}
    envs = " ".join(f"{k}={shlex.quote(v)}" for k, v in base.items() if re.match(r"^[A-Za-z_]\w*$", k))
    shell = base.get("SHELL", "/bin/sh")
    return f"cd {shlex.quote(home)} && env -i {envs} {shlex.quote(shell)} -c {shlex.quote(cmd)}"
