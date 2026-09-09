"""
Salesforce Marketing Cloud (SFMC) auth — Client Credentials flow.

Important: Marketing Cloud is a *separate product* from Sales Cloud /
Service Cloud. It does not share objects, org data, or an OAuth server
with core Salesforce — it has its own subdomain, its own "API Integration"
connected app (server-to-server, no user login), and its own REST API
(https://<subdomain>.rest.marketingcloudapis.com).

Because it's a client-credentials (machine-to-machine) grant, there's no
browser step — `setup_marketing_cloud_auth.py` just verifies the
credentials in .env work and caches the short-lived access token.
"""

import json
import os
import time
from pathlib import Path

import requests

TOKEN_FILE = Path(__file__).parent / ".mc_token.json"

_memory_cache: dict = {}  # fallback when the filesystem is read-only (common on container platforms)


def _load_config():
    subdomain = os.environ.get("MC_SUBDOMAIN")
    client_id = os.environ.get("MC_CLIENT_ID")
    client_secret = os.environ.get("MC_CLIENT_SECRET")
    account_id = os.environ.get("MC_ACCOUNT_ID")  # optional MID, for Business Units
    missing = [n for n, v in [
        ("MC_SUBDOMAIN", subdomain), ("MC_CLIENT_ID", client_id), ("MC_CLIENT_SECRET", client_secret)
    ] if not v]
    if missing:
        raise RuntimeError(
            f"Missing Marketing Cloud config: {', '.join(missing)}. "
            "Set these in .env from your MC API Integration package "
            "(Setup > Apps > Installed Packages)."
        )
    return subdomain, client_id, client_secret, account_id


def _fetch_token():
    subdomain, client_id, client_secret, account_id = _load_config()
    auth_url = f"https://{subdomain}.auth.marketingcloudapis.com/v2/token"
    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if account_id:
        body["account_id"] = account_id

    resp = requests.post(auth_url, json=body, timeout=30)
    resp.raise_for_status()
    token = resp.json()
    payload = {
        "access_token": token["access_token"],
        "rest_base": token.get("rest_instance_url", f"https://{subdomain}.rest.marketingcloudapis.com"),
        "expires_at": time.time() + token.get("expires_in", 1200) - 60,  # refresh a minute early
    }
    _memory_cache.update(payload)
    try:
        TOKEN_FILE.write_text(json.dumps(payload, indent=2))
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass  # read-only filesystem (e.g. some container platforms) — memory cache still works
    return payload


def get_valid_access_token() -> tuple[str, str]:
    """Returns (access_token, rest_base_url), fetching or refreshing as needed."""
    if _memory_cache.get("expires_at", 0) > time.time():
        return _memory_cache["access_token"], _memory_cache["rest_base"]

    if TOKEN_FILE.exists():
        try:
            cached = json.loads(TOKEN_FILE.read_text())
            if cached.get("expires_at", 0) > time.time():
                _memory_cache.update(cached)
                return cached["access_token"], cached["rest_base"]
        except OSError:
            pass

    payload = _fetch_token()
    return payload["access_token"], payload["rest_base"]


def verify_connection():
    """Used by setup_marketing_cloud_auth.py to confirm .env credentials work."""
    token, rest_base = get_valid_access_token()
    print(f"Connected to Marketing Cloud REST API at:\n  {rest_base}")
    return token, rest_base
