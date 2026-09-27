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
import hashlib
import http.server
import json
import os
import secrets
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


def _save_tokens(tokens: dict):
    payload = {
        "refresh_token": tokens["refresh_token"],
        "access_token": tokens.get("access_token"),
        "instance_url": tokens["instance_url"],
        "saved_at": time.time(),
    }
    TOKEN_FILE.write_text(json.dumps(payload, indent=2))
    TOKEN_FILE.chmod(0o600)


def get_valid_access_token(max_retries: int = 3) -> tuple[str, str]:
    """
    Returns (access_token, instance_url), refreshing via the stored
    refresh token.

    Two ways this can get a refresh token, checked in order:
      1. SF_REFRESH_TOKEN env var — used by the remote/cloud variant,
         where you authenticate once locally and inject the resulting
         refresh token as a platform secret (see README's "Deploying to
         the cloud" section). No local token file needed or written.
      2. .salesforce_token.json on disk — used by the local variant,
         written by `python3 setup_salesforce_auth.py`.

    RETRIES, AND WHY THEY RE-READ THE TOKEN EACH TIME: with several
    processes sharing one Connected App (the Slack integrations each
    run as their own process), a refresh-token-rotation policy means
    each use immediately invalidates the previous token. If Process A
    rotates the token a moment before Process B tries to use the
    now-stale one it already had in memory, B's request fails with
    invalid_grant -- even though a perfectly valid token exists on
    disk by that point, because A already saved it. A naive retry that
    reuses the same in-memory token would just fail identically every
    time. So each retry re-reads the token file fresh from disk rather
    than reusing what this call started with -- if another process's
    rotation has already landed, the retry picks it up and succeeds.
    A brief pause between attempts gives an in-flight rotation from
    another process a moment to finish saving before the next read.

    This does NOT paper over a genuinely dead session (password
    changed, refresh token actually expired, connected app revoked) --
    those fail identically on every retry, re-reading the file changes
    nothing, and the real error still surfaces after max_retries.
    """
    login_url, client_id, client_secret, _ = _load_config()

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
            return (tokens["access_token"], tokens["instance_url"])

        last_error = RuntimeError(
            f"Salesforce refresh failed ({resp.status_code}): {resp.text}\n"
            "The session may have been revoked. Re-run setup_salesforce_auth.py "
            "(local) or refresh SF_REFRESH_TOKEN (remote)."
        )
        if attempt < max_retries - 1:
            time.sleep(1.5)

    raise last_error
