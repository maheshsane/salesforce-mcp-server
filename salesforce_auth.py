"""
Salesforce OAuth 2.0 — Authorization Code flow with PKCE.

Covers Sales Cloud and Service Cloud (they share one core REST API and one
OAuth authorization server per org). This module is used two ways:

  1. `python3 setup_salesforce_auth.py` — run once, interactively, to open a
     browser, log in, and store a refresh token on disk.
  2. Imported by `sf_client.py` at server start to silently exchange the
     stored refresh token for a fresh access token (and refresh again
     whenever the org says the token expired).

Nothing here ever hardcodes a token. Everything sensitive lives in
`.env` (client id, optional secret) or `.salesforce_token.json`
(refresh token, on disk, gitignored) — never in source.
"""

import base64
import contextlib
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

TOKEN_FILE = Path(__file__).parent / ".salesforce_token.json"

DEFAULT_REDIRECT_PORT = 8765
DEFAULT_REDIRECT_URI = f"http://localhost:{DEFAULT_REDIRECT_PORT}/callback"
DEFAULT_SCOPES = "api refresh_token offline_access"


class OAuthCallbackServer(http.server.HTTPServer):
    """Tiny local HTTP server that exists only to catch the OAuth redirect."""
    auth_code = None
    error = None


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in params:
            self.server.auth_code = params["code"][0]
            body = "<html><body><h2>Salesforce connected. You can close this tab.</h2></body></html>"
        else:
            self.server.error = params.get("error_description", params.get("error", ["Unknown error"]))[0]
            body = f"<html><body><h2>Authorization failed: {self.server.error}</h2></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass  # keep terminal output quiet


def _make_pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    return verifier, challenge


def _load_config():
    login_url = os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com").rstrip("/")
    client_id = os.environ.get("SF_CLIENT_ID")
    client_secret = os.environ.get("SF_CLIENT_SECRET")  # optional for PKCE public clients
    redirect_uri = os.environ.get("SF_REDIRECT_URI", DEFAULT_REDIRECT_URI)
    if not client_id:
        raise RuntimeError(
            "SF_CLIENT_ID is not set. Copy .env.example to .env and fill in the "
            "Consumer Key from your Salesforce Connected App."
        )
    return login_url, client_id, client_secret, redirect_uri


def run_login_flow():
    """
    Opens a browser for the user to log into their Salesforce org, catches
    the redirect locally, exchanges the code for tokens, and saves the
    refresh token to disk. Intended to be run once via setup_salesforce_auth.py.
    """
    login_url, client_id, client_secret, redirect_uri = _load_config()
    verifier, challenge = _make_pkce_pair()
    state = secrets.token_urlsafe(16)

    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": DEFAULT_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{login_url}/services/oauth2/authorize?{urllib.parse.urlencode(auth_params)}"

    port = urllib.parse.urlparse(redirect_uri).port or DEFAULT_REDIRECT_PORT
    httpd = OAuthCallbackServer(("localhost", port), OAuthCallbackHandler)

    print(f"Opening a browser to log into Salesforce at:\n  {login_url}\n")
    print("If it doesn't open automatically, visit this URL:")
    print(auth_url + "\n")
    webbrowser.open(auth_url)

    server_thread = threading.Thread(target=httpd.handle_request)
    server_thread.start()
    server_thread.join(timeout=180)

    if httpd.error:
        raise RuntimeError(f"Salesforce login failed: {httpd.error}")
    if not httpd.auth_code:
        raise RuntimeError("Timed out waiting for the Salesforce login redirect.")

    token_params = {
        "grant_type": "authorization_code",
        "code": httpd.auth_code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }
    if client_secret:
        token_params["client_secret"] = client_secret

    resp = requests.post(f"{login_url}/services/oauth2/token", data=token_params, timeout=30)
    resp.raise_for_status()
    tokens = resp.json()

    _save_tokens(tokens)
    print(f"\nConnected. Instance URL: {tokens['instance_url']}")
    return tokens


def _lock_path() -> Path:
    # Derived at call time from TOKEN_FILE rather than fixed at import,
    # so it always sits next to whichever token file is in use.
    return TOKEN_FILE.with_name(TOKEN_FILE.name + ".lock")


def _save_tokens(tokens: dict):
    """
    Writes the token file ATOMICALLY: write a temp file, then rename it
    over the real one. Several processes read this file without holding
    any lock, and a plain write_text() truncates the file first, so a
    reader could catch it empty or half-written. A rename is
    all-or-nothing -- a reader sees the old file or the new one, never
    a partial one.

    Also records when the access token in here should be considered
    stale (access_expires_at), which is what lets OTHER processes
    reuse it instead of each doing their own refresh -- see
    get_valid_access_token.
    """
    payload = {
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens.get("access_token"),
        "instance_url": tokens["instance_url"],
        "saved_at": time.time(),
    }
    if tokens.get("access_token"):
        payload["access_expires_at"] = time.time() + CACHE_SECONDS

    tmp = TOKEN_FILE.with_name(f"{TOKEN_FILE.name}.tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(payload, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, TOKEN_FILE)


_cached_token = None  # (access_token, instance_url, expires_at_epoch_seconds) or None
CACHE_SECONDS = 20 * 60  # conservative -- well under any realistic session length


def _read_shared_access_token():
    """
    (access_token, instance_url, expires_at) if this process, or any
    other, left a still-fresh access token in the token file; else None.
    Safe to call without the lock: _save_tokens replaces the file
    atomically, so this never sees a half-written file.
    """
    try:
        data = json.loads(TOKEN_FILE.read_text())
    except (OSError, ValueError):
        return None
    token = data.get("access_token")
    url = data.get("instance_url")
    expires_at = data.get("access_expires_at")
    if token and url and expires_at and time.time() < expires_at:
        return (token, url, expires_at)
    return None


@contextlib.contextmanager
def _refresh_lock(timeout: float = 45.0):
    """
    Cross-process lock around the refresh-token exchange. Needed because
    this org rotates refresh tokens: every refresh returns a NEW refresh
    token and retires the one it just used (confirmed live -- three
    consecutive refreshes produced three different tokens). The refresh
    token is therefore a single-use credential shared through one file.
    Two processes refreshing at once means one presents an
    already-retired token, and depending on how the provider treats
    reuse of a retired token, that can kill the whole chain rather than
    just failing one call.

    flock is released automatically by the OS if the holder dies, so a
    crashed process can't leave this stuck. Where locking isn't
    available (non-POSIX systems, read-only filesystems) this quietly
    does nothing rather than break auth entirely.
    """
    try:
        import fcntl
    except ImportError:
        yield
        return
    try:
        handle = open(_lock_path(), "a+")
    except OSError:
        yield
        return
    try:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise RuntimeError(
                        "Timed out waiting for the Salesforce token refresh lock "
                        f"({_lock_path()}) -- another process may be stuck mid-refresh."
                    )
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def _adopt(shared) -> tuple[str, str]:
    global _cached_token
    _cached_token = shared
    return (shared[0], shared[1])


def get_valid_access_token(max_retries: int = 3, force_refresh: bool = False) -> tuple[str, str]:
    """
    Returns (access_token, instance_url).

    Where the refresh token comes from, checked in order:
      1. SF_REFRESH_TOKEN env var -- the remote/cloud variant, where you
         authenticate once locally and inject the resulting refresh
         token as a platform secret. No local token file required.
      2. .salesforce_token.json -- the local variant, written by
         `python3 setup_salesforce_auth.py`.

    HOW THIS AVOIDS REFRESHING, AND WHY IT MATTERS: refresh tokens rotate
    here, so every refresh is a risky, single-use operation on a
    credential shared by every process -- and this project runs several
    at once, each alert additionally spawns a fresh server.py process
    with an empty in-memory cache, and every Salesforce call used to
    trigger its own refresh. Three layers now keep refreshes rare:
      1. This process's in-memory cache.
      2. The access token stored in the shared token file -- so a
         freshly spawned process reuses what any other process already
         obtained, instead of refreshing on its first call.
      3. Only when both are empty, a real refresh, serialized behind a
         cross-process lock, re-checking the shared token once the lock
         is held (another process may have just refreshed while we
         waited).

    force_refresh=True means the caller just got a 401 using the token
    we handed out, so that token is dead whatever the clock says. If
    another process has meanwhile stored a DIFFERENT token, we adopt
    that one rather than refreshing again -- otherwise several
    processes hitting the same dead token would each rotate the refresh
    token in turn.

    The retry loop still re-reads the token source on every attempt, for
    transient failures. It does not paper over a genuinely dead session
    (password changed, app revoked): those fail identically each time
    and the real error surfaces after max_retries.
    """
    global _cached_token
    dead_token = None
    if force_refresh:
        if _cached_token is not None:
            dead_token = _cached_token[0]
        _cached_token = None

    if _cached_token is not None:
        access_token, instance_url, expires_at = _cached_token
        if time.time() < expires_at:
            return (access_token, instance_url)
        _cached_token = None

    shared = _read_shared_access_token()
    if shared and shared[0] != dead_token:
        return _adopt(shared)

    login_url, client_id, client_secret, _ = _load_config()

    with _refresh_lock():
        shared = _read_shared_access_token()
        if shared and shared[0] != dead_token:
            return _adopt(shared)

        last_error = None
        for attempt in range(max_retries):
            env_refresh_token = os.environ.get("SF_REFRESH_TOKEN")
            if env_refresh_token:
                refresh_token = env_refresh_token
            elif TOKEN_FILE.exists():
                refresh_token = json.loads(TOKEN_FILE.read_text())["refresh_token"]
            else:
                raise RuntimeError(
                    "No Salesforce session found. Run `python3 setup_salesforce_auth.py` "
                    "locally first, or set SF_REFRESH_TOKEN in this environment."
                )

            refresh_params = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
            if client_secret:
                refresh_params["client_secret"] = client_secret

            resp = requests.post(f"{login_url}/services/oauth2/token", data=refresh_params, timeout=30)

            if resp.status_code == 200:
                tokens = resp.json()
                tokens.setdefault("refresh_token", refresh_token)
                try:
                    _save_tokens(tokens)
                except OSError:
                    pass
                _cached_token = (
                    tokens["access_token"], tokens["instance_url"], time.time() + CACHE_SECONDS,
                )
                # One line per REAL refresh, so refresh frequency is visible in
                # the logs (grep -c "token refreshed" logs/*.log) rather than
                # something to guess at. stderr, so it never touches the MCP
                # stdio protocol on stdout.
                print(
                    f"[salesforce_auth] token refreshed (pid {os.getpid()}"
                    f"{', after a 401' if force_refresh else ''})",
                    file=sys.stderr, flush=True,
                )
                return (tokens["access_token"], tokens["instance_url"])

            last_error = RuntimeError(
                f"Salesforce refresh failed ({resp.status_code}): {resp.text}\n"
                "The session may have been revoked. Re-run setup_salesforce_auth.py "
                "(local) or refresh SF_REFRESH_TOKEN (remote)."
            )
            if attempt < max_retries - 1:
                time.sleep(1.5)

        raise last_error
