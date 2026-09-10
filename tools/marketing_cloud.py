"""
Marketing Cloud tools — separate Salesforce product, separate API,
separate connection (see marketing_cloud_auth.py, mc_client.py). Needs
its own MC_* credentials in .env; core Salesforce Sales/Service Cloud
tools work independently of whether this module is configured.
"""

import json

import mc_client
from mcp_instance import mcp


@mcp.tool()
def mc_list_data_extensions() -> str:
    """
    List Marketing Cloud Data Extensions (its equivalent of tables) —
    subscriber lists, campaign audiences, engagement history, etc.
    Requires Marketing Cloud credentials set up separately from core
    Salesforce (see setup_marketing_cloud_auth.py); this is a different
    Salesforce product with its own API.
    """
    return json.dumps(mc_client.list_data_extensions(), indent=2)


@mcp.tool()
def mc_query_data_extension(customer_key: str, page: int = 1) -> str:
    """
    Retrieve rows from a Marketing Cloud Data Extension by its customer
    key (find it via mc_list_data_extensions first). Returns 50 rows
    per page.
    """
    return json.dumps(mc_client.query_data_extension_rows(customer_key, page=page), indent=2)


@mcp.tool()
def mc_list_journeys(page: int = 1) -> str:
    """
    List Marketing Cloud Journey Builder journeys (automated multi-step
    campaigns) — useful for tying marketing engagement back to
    PreSales/CS account activity in core Salesforce.
    """
    return json.dumps(mc_client.list_journeys(page=page), indent=2)


@mcp.tool()
def mc_upsert_data_extension_rows(customer_key: str, primary_key_fields: list[str], rows: list[dict]) -> str:
    """
    MUTATING — inserts or updates rows in a Marketing Cloud Data
    Extension. Existing rows are matched on primary_key_fields, which
    must match the Data Extension's actual configured primary key
    column(s) — find those via the DE's setup page if unsure; getting
    this wrong either fails the call or silently inserts duplicates
    instead of updating. Each row is a full dict of column name to
    value, including the primary key columns. Keep batches to roughly
    50 rows / 50 columns per call.

    There's no row-level delete tool to pair with this — Marketing
    Cloud's REST API doesn't expose one (see mc_client.py for why).

    Example: adding someone to a list-style Data Extension, or
    updating a status field a journey's entry criteria will pick up.
      customer_key="Subscribers_2026"
      primary_key_fields=["SubscriberKey"]
      rows=[{"SubscriberKey": "12345", "Status": "Active", "Segment": "VIP"}]
    """
    result = mc_client.upsert_data_extension_rows(customer_key, primary_key_fields, rows)
    return json.dumps(result, indent=2)
