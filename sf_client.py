"""
Thin, generic client over the core Salesforce REST API.

This is deliberately object-agnostic — it queries whatever SOQL you give
it, against whatever standard or custom objects exist in the connected
org (Accounts, Opportunities, Cases, Leads, Campaigns, Contacts, and any
custom objects). That's what makes it usable across PreSales, Marketing,
and Customer Success rather than locked to one team's schema.

Every call auto-refreshes the access token once on a 401 and retries.
"""

import requests

import salesforce_auth

API_VERSION = "v60.0"


class SalesforceError(RuntimeError):
    pass


def _request(method: str, path: str, **kwargs) -> dict:
    access_token, instance_url = salesforce_auth.get_valid_access_token()
    url = f"{instance_url}{path}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {access_token}"

    resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)

    if resp.status_code == 401:
        # Token may have been invalidated between our cache check and this
        # call (e.g. revoked, or clock skew). Force one refresh and retry.
        # force_refresh=True is what makes this actually true now that
        # salesforce_auth caches tokens -- without it, this call would
        # just get the same cached (dead) token back.
        access_token, instance_url = salesforce_auth.get_valid_access_token(force_refresh=True)
        headers["Authorization"] = f"Bearer {access_token}"
        resp = requests.request(method, f"{instance_url}{path}", headers=headers, timeout=30, **kwargs)

    if not resp.ok:
        raise SalesforceError(f"Salesforce API error {resp.status_code}: {resp.text}")
    if resp.text:
        return resp.json()
    return {}


def query(soql: str) -> list[dict]:
    """
    Runs a SOQL query and returns every record, following pagination
    automatically. Works against any object the connected user can see:
    Account, Opportunity, Case, Lead, Contact, Campaign, CampaignMember,
    Task, custom objects (__c), etc.
    """
    records: list[dict] = []
    path = f"/services/data/{API_VERSION}/query/?q={requests.utils.quote(soql)}"
    while path:
        page = _request("GET", path)
        records.extend(page.get("records", []))
        next_url = page.get("nextRecordsUrl")
        path = next_url if next_url else None
    # Strip Salesforce's internal "attributes" metadata block from each record
    for r in records:
        r.pop("attributes", None)
    return records


def describe_object(object_name: str) -> dict:
    """
    Returns field names, types, and picklist values for an object — use
    this before writing a SOQL query against an object you haven't
    queried before, so field names are exact.
    """
    data = _request("GET", f"/services/data/{API_VERSION}/sobjects/{object_name}/describe/")
    return {
        "name": data["name"],
        "label": data["label"],
        "fields": [
            {
                "name": f["name"],
                "type": f["type"],
                "picklist_values": [p["value"] for p in f.get("picklistValues", []) if p.get("active")],
            }
            for f in data["fields"]
        ],
    }


def create(object_name: str, fields: dict) -> dict:
    """
    Creates a record on the given object (e.g. 'Task', 'Case', 'Contact')
    with the given field values. This mutates live Salesforce data —
    only call it from tools that clearly document themselves as
    mutating, and only with data the user asked to be written.
    Returns {"id": ..., "success": ...}.
    """
    return _request(
        "POST",
        f"/services/data/{API_VERSION}/sobjects/{object_name}/",
        json=fields,
    )


def update(object_name: str, record_id: str, fields: dict) -> None:
    """
    Updates specific fields on an existing record (PATCH) — only the
    fields you pass are changed; anything else on the record is left
    alone. Salesforce returns no body on success (204), so this
    function returns None; a non-2xx status raises SalesforceError.
    """
    _request(
        "PATCH",
        f"/services/data/{API_VERSION}/sobjects/{object_name}/{record_id}",
        json=fields,
    )


def delete(object_name: str, record_id: str) -> None:
    """
    Deletes a record. Salesforce moves deleted records to the Recycle
    Bin rather than erasing them immediately — recoverable for about
    15 days under default org settings, permanently gone after that or
    if the bin is emptied manually. Returns None on success (204); a
    non-2xx status raises SalesforceError.
    """
    _request(
        "DELETE",
        f"/services/data/{API_VERSION}/sobjects/{object_name}/{record_id}",
    )


def list_objects() -> list[dict]:
    """Returns every object (standard and custom) queryable in this org."""
    data = _request("GET", f"/services/data/{API_VERSION}/sobjects/")
    return [
        {"name": s["name"], "label": s["label"], "custom": s["custom"], "queryable": s["queryable"]}
        for s in data["sobjects"]
        if s["queryable"]
    ]
