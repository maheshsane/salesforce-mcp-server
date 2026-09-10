"""
Thin client over the Marketing Cloud REST API.

Scoped intentionally to the pieces most useful for cross-functional
analysis (what's in a Data Extension, what campaigns/journeys exist) rather
than the full SFMC surface, which also covers email/SMS/push authoring,
automations, and content — a much bigger API than core Salesforce's.
Extend `_request` calls here if you need more of it.
"""

import requests

import marketing_cloud_auth


class MarketingCloudError(RuntimeError):
    pass


def _request(method: str, path: str, **kwargs) -> dict:
    access_token, rest_base = marketing_cloud_auth.get_valid_access_token()
    url = f"{rest_base}{path}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {access_token}"
    resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)
    if not resp.ok:
        raise MarketingCloudError(f"Marketing Cloud API error {resp.status_code}: {resp.text}")
    return resp.json() if resp.text else {}


def list_data_extensions() -> list[dict]:
    """Lists Data Extensions (Marketing Cloud's tables) visible to this API integration."""
    data = _request("GET", "/data/v1/customobjectdata")
    return data.get("items", data)


def query_data_extension_rows(customer_key: str, page: int = 1, page_size: int = 50) -> dict:
    """
    Returns rows from a Data Extension by its customer key. Use
    list_data_extensions() first to find the right key.
    """
    return _request(
        "GET",
        f"/data/v1/customobjectdata/key/{customer_key}/rowset"
        f"?$page={page}&$pageSize={page_size}",
    )


def list_journeys(page: int = 1, page_size: int = 50) -> dict:
    """Lists Journey Builder journeys (campaign-style customer journeys)."""
    return _request("GET", f"/interaction/v1/interactions?$page={page}&$pageSize={page_size}")


def upsert_data_extension_rows(customer_key: str, primary_key_fields: list[str], rows: list[dict]) -> dict:
    """
    Inserts or updates rows in a Data Extension via its external key,
    using POST /hub/v1/dataevents/key:{key}/rowset. Existing rows are
    matched on primary_key_fields (must match the DE's actual primary
    key column(s) — check the DE's setup page if unsure); anything
    else in the row updates in place, a new primary key inserts a new
    row. Marketing Cloud caps this call at roughly 50 columns and 50
    records — send larger batches as multiple calls.

    Note: there is no equivalent single-row delete here. Marketing
    Cloud's REST API doesn't expose one — the only REST delete
    available operates on an entire Data Extension, not a row. Row-level
    delete exists only via the older SOAP API, not implemented here.
    """
    items = [
        {"keys": {k: row[k] for k in primary_key_fields if k in row}, "values": row}
        for row in rows
    ]
    return _request(
        "POST",
        f"/hub/v1/dataevents/key:{customer_key}/rowset",
        json={"items": items},
    )
