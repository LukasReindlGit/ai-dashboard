"""Login: credentials, first-run setup, TOTP, sessions and failed-login limits.

Credentials come from the environment (DASHBOARD_PASSWORD, TOTP_SECRET) or, if
those are empty, from data/auth.json (scrypt hash of the password + TOTP secret),
which the setup screen writes. While either is missing the dashboard shows only
the setup screen: whoever opens it first sets the login, so finish it right after
deploying. If a password already exists (only the TOTP is missing), setup asks for
it. Delete data/auth.json and restart to set it up again.

Sessions are server-side (data/sessions.json holds sha256 of each token), so
signing out really ends a session and deleting the file ends all of them.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import struct
import threading
import time
import urllib.parse
from pathlib import Path

STATE_DIR = Path(os.environ.get("STATE_DIR", "/data"))
AUTH_FILE = STATE_DIR / "auth.json"
SESSIONS_FILE = STATE_DIR / "sessions.json"
SESSION_TTL = 7 * 24 * 3600
MIN_PASSWORD = 12
ISSUER = "AI Ops"


def b32_secret(s):
    s = re.sub(r"[\s=-]", "", s or "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8)) if s else b""


def write_private(path, text):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.chmod(0o600)
    tmp.replace(path)


# ---------------------------------------------------------------- credentials

CREDS = {"password": None, "totp": b""}  # password: ("plain", pw) | ("scrypt", stored)


def load_creds():
    try:
        saved = json.loads(AUTH_FILE.read_text())
    except (OSError, ValueError):
        saved = {}
    env_pw = os.environ.get("DASHBOARD_PASSWORD", "")
    CREDS["password"] = ("plain", env_pw) if env_pw else ("scrypt", saved["password"]) if saved.get("password") else None
    CREDS["totp"] = b32_secret(os.environ.get("TOTP_SECRET") or saved.get("totp"))


def needs_setup():
    return not CREDS["password"] or not CREDS["totp"]


def hash_password(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${h.hex()}"


def password_ok(pw):
    cred = CREDS["password"]
    if not cred:
        return False
    kind, stored = cred
    if kind == "plain":
        return hmac.compare_digest(pw.encode(), stored.encode())
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
    except ValueError:
        return False
    return hmac.compare_digest(got.hex(), h)


# ---------------------------------------------------------------- TOTP

TOTP_USED = {"step": 0}  # last accepted time step: a code works once


def totp(key, step):
    h = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000)


def totp_ok(code, key=None):
    key = key or CREDS["totp"]
    code = re.sub(r"\s", "", code or "")
    now = int(time.time()) // 30
    for step in (now - 1, now, now + 1):
        if step > TOTP_USED["step"] and hmac.compare_digest(code, totp(key, step)):
            TOTP_USED["step"] = step
            return True
    return False


def otpauth_uri(secret_b32):
    host = os.environ.get("VIRTUAL_HOST", "").split(",")[0].strip() or socket.gethostname()
    label = urllib.parse.quote(f"{ISSUER}:{host}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={urllib.parse.quote(ISSUER)}"


def qr_svg(text):
    """The text as an inline SVG QR code, or "" if python3-qrcode isn't installed."""
    try:
        import qrcode
    except ImportError:
        return ""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    m = qr.get_matrix()
    dark = "".join(f"M{x} {y}h1v1h-1z" for y, row in enumerate(m) for x, v in enumerate(row) if v)
    n = len(m)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" shape-rendering="crispEdges" '
            f'role="img" aria-label="QR code"><rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path d="{dark}" fill="#000"/></svg>')


# ---------------------------------------------------------------- first-run setup

SETUP = {"pending": {}}  # pending: id -> {"pw", "totp", "exp"}
SETUP_LOCK = threading.Lock()


def startup():
    load_creds()
    if needs_setup():
        print("SETUP NEEDED: open the dashboard and set the login now (whoever opens it first can).", flush=True)


def setup_start(pw, pw2):
    """Step 1: the new password (or, if one is already set, that password), and a TOTP
    secret. Returns (pending id, secret) if a TOTP still has to be confirmed, else None
    (then setup is complete)."""
    pw_hash = None
    if CREDS["password"]:
        if not password_ok(pw):
            raise PermissionError("Wrong password.")
    else:
        if len(pw) < MIN_PASSWORD:
            raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
        if pw != pw2:
            raise ValueError("The passwords don't match.")
        pw_hash = hash_password(pw)
    if CREDS["totp"]:
        setup_finish_with(pw_hash, None)
        return None
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    pid = secrets.token_urlsafe(16)
    with SETUP_LOCK:
        now = time.time()
        SETUP["pending"] = {k: v for k, v in SETUP["pending"].items() if v["exp"] > now}
        SETUP["pending"][pid] = {"pw": pw_hash, "totp": secret, "exp": now + 900}
    return pid, secret


def setup_pending(pid):
    p = SETUP["pending"].get(pid or "")
    return p if p and p["exp"] > time.time() else None


def setup_confirm(pid, code):
    """Step 2: the first authenticator code proves the app has the secret."""
    p = setup_pending(pid)
    if not p:
        raise ValueError("This setup step expired. Start again.")
    if not totp_ok(code, b32_secret(p["totp"])):
        raise ValueError("That code is wrong. Check the time on your phone and try the next one.")
    setup_finish_with(p["pw"], p["totp"])


def setup_finish_with(pw_hash, totp_secret):
    with SETUP_LOCK:
        try:
            saved = json.loads(AUTH_FILE.read_text())
        except (OSError, ValueError):
            saved = {}
        if pw_hash:
            saved["password"] = pw_hash
        if totp_secret:
            saved["totp"] = totp_secret
        saved["created"] = int(time.time())
        write_private(AUTH_FILE, json.dumps(saved, indent=1))
        load_creds()
        if not needs_setup():
            SETUP["pending"] = {}


# ---------------------------------------------------------------- sessions

SESSIONS_LOCK = threading.Lock()


def token_id(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def load_sessions():
    try:
        live = json.loads(SESSIONS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in live.items() if v.get("exp", 0) > now}


def make_token(ip):
    tok = secrets.token_urlsafe(32)
    with SESSIONS_LOCK:
        live = load_sessions()
        live[token_id(tok)] = {"exp": int(time.time()) + SESSION_TTL, "created": int(time.time()), "ip": ip}
        write_private(SESSIONS_FILE, json.dumps(live))
    return tok


def valid_token(tok):
    if not tok or needs_setup():
        return False
    with SESSIONS_LOCK:
        return token_id(tok) in load_sessions()


def drop_token(tok):
    with SESSIONS_LOCK:
        live = load_sessions()
        if live.pop(token_id(tok), None):
            write_private(SESSIONS_FILE, json.dumps(live))


# ---------------------------------------------------------------- failed-login limits

# Per client IP, and in total (so many IPs can't brute-force either).
FAILS = {}  # ip -> [timestamps]
ALL_FAILS = []
FAILS_LOCK = threading.Lock()


def throttled(ip):
    now = time.time()
    with FAILS_LOCK:
        for k in list(FAILS):
            FAILS[k] = [t for t in FAILS[k] if now - t < 900]
            if not FAILS[k]:
                del FAILS[k]
        ALL_FAILS[:] = [t for t in ALL_FAILS if now - t < 3600]
        return len(FAILS.get(ip, [])) >= 10 or len(ALL_FAILS) >= 50


def record_fail(ip):
    with FAILS_LOCK:
        FAILS.setdefault(ip, []).append(time.time())
        ALL_FAILS.append(time.time())


def clear_fails(ip):
    with FAILS_LOCK:
        FAILS.pop(ip, None)


# X-Real-IP is only believed from the reverse proxy; anyone else could forge it.
TRUSTED_PROXY = os.environ.get("TRUSTED_PROXY", "nginx-proxy")
PROXY_IPS = {"t": 0, "ips": set()}


def proxy_ips():
    if time.time() - PROXY_IPS["t"] > 60:
        try:
            PROXY_IPS["ips"] = {a[4][0] for a in socket.getaddrinfo(TRUSTED_PROXY, None)}
        except OSError:
            PROXY_IPS["ips"] = set()
        PROXY_IPS["t"] = time.time()
    return PROXY_IPS["ips"]
