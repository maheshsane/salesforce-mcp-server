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

CONFIDENCE NOTE, updated after live verification: the query-sql endpoint
requires API v62.0 or later -- v60.0 (sf_client.py's version, fine for
every other Salesforce call in this project) 404s on this endpoint
outright, confirmed against a real org. Data 360 gets its own,
independent API_VERSION below rather than inheriting sf_client's, so
this fix doesn't touch the version every other module already relies on.

The single-page response shape is now confirmed against a real query,
not just docs -- and it was wrong before: `queryId` and
`completionStatus` are nested under a top-level "status" object, not
top-level themselves. The original code read them from the wrong place,
so query_id was always None, which meant the pagination loop's
condition (`while not done and query_id`) could never trigger -- any
query genuinely needing a second page would have silently returned only
page 1, no error. Fixed below.

The confirmed "done" value is completionStatus == "ResultsProduced".
What a genuine "there are more pages, keep polling" value looks like is
still NOT verified against a live multi-page result -- this project
hasn't hit a result set large enough to force pagination yet. If you do,
print the raw response on that call and confirm the pagination path
before trusting it.
"""

import requests

import salesforce_auth

API_VERSION = "v62.0"  # independent of sf_client.API_VERSION -- see note above


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

    # queryId and completionStatus live under "status", confirmed against
    # a real response -- not top-level, which is what the original
    # (unverified) version of this code assumed.
    status = payload.get("status", {})
    query_id = status.get("queryId")
    done = status.get("completionStatus") == "ResultsProduced"

    pages_fetched = 1
    while not done and query_id and pages_fetched < max_pages:
        rows_url = f"{instance_url}/services/data/{API_VERSION}/ssot/query-sql/{query_id}/rows"
        resp = requests.get(rows_url, headers=headers, timeout=60)
        if not resp.ok:
            raise DataCloudError(f"Data 360 pagination error {resp.status_code}: {resp.text}")
        page = resp.json()
        rows.extend(dict(zip(columns, row)) for row in page.get("data", []))
        page_status = page.get("status", {})
        done = page_status.get("completionStatus") == "ResultsProduced"
        pages_fetched += 1

    return rows
