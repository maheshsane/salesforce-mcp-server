"""
Client for Salesforce Data 360 (rebranded from "Data Cloud" in October
2025) via the Query Connect API.

Unlike Marketing Cloud, this does NOT need separate credentials. Data
360 has two parallel API families:

  1. The "Direct API" (/api/v1, /api/v2, hosted at a tenant-specific
     *.360a.salesforce.com URL) -- requires its own two-step token
     exchange (org OAuth token -> POST {instance_url}/services/a360/token
     -> a Data 360-specific JWT). A genuinely separate connection, the
     same shape as Marketing Cloud's.
  2. The "Query Connect API" (/services/data/vXX.0/ssot/query-sql,
     hosted at the normal org instance_url) -- runs on the exact same
     OAuth session as sf_client.py. Salesforce's own docs recommend
     this one "for most integration scenarios."

This module implements #2, so Data 360 querying works the moment
Sales/Service Cloud auth (setup_salesforce_auth.py) is already set up
-- no new .env variables, no new setup script. If you specifically
need the Direct API (e.g. for an endpoint only it exposes), that's a
genuinely separate auth module to build -- see CONTRIBUTING.md.

CONFIDENCE NOTE: the single-page query response shape below (data +
metadata + queryId) is confirmed against Salesforce's published
examples. The multi-page continuation shape (what exactly signals
"more rows available" on this specific endpoint) was not independently
verified against a live response at the time this was written -- the
pagination logic here is a best-effort reading of the docs, checked
defensively. If you hit a large result set, verify page 2 actually
returns new rows before trusting it blindly.
"""

import requests

import sf_client
import salesforce_auth

API_VERSION = sf_client.API_VERSION


class DataCloudError(RuntimeError):
    pass


def query(sql: str, max_pages: int = 20) -> list[dict]:
    """
    Runs an ANSI SQL query (not SOQL) against Data 360 objects and
    returns every matching row as a list of dicts, column names taken
    from the response's own metadata. Follows pagination best-effort
    up to max_pages.
    """
    access_token, instance_url = salesforce_auth.get_valid_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}
    base = f"{instance_url}/services/data/{API_VERSION}/ssot/query-sql"

    resp = requests.post(base, headers=headers, json={"sql": sql}, timeout=60)
    if not resp.ok:
        raise DataCloudError(f"Data 360 query error {resp.status_code}: {resp.text}")
    payload = resp.json()

    columns = [c.get("name") for c in payload.get("metadata", [])]
    rows = [dict(zip(columns, row)) for row in payload.get("data", [])]

    query_id = payload.get("queryId")
    # Defensive completion check: different Data 360 query endpoints have
    # used different field names for "is there more" across versions
    # (done / completionStatus). Treat absence of a next-page signal as done.
    done = payload.get("done", True) or payload.get("completionStatus") == "ResultsComplete"

    pages_fetched = 1
    while not done and query_id and pages_fetched < max_pages:
        rows_url = f"{instance_url}/services/data/{API_VERSION}/ssot/query-sql/{query_id}/rows"
        resp = requests.get(rows_url, headers=headers, timeout=60)
        if not resp.ok:
            raise DataCloudError(f"Data 360 pagination error {resp.status_code}: {resp.text}")
        page = resp.json()
        rows.extend(dict(zip(columns, row)) for row in page.get("data", []))
        done = page.get("done", True) or page.get("completionStatus") == "ResultsComplete"
        pages_fetched += 1

    return rows
