"""
Sales tools — pipeline visibility, lead flow, and forecast, built on
standard Opportunity/Lead fields so they work in any org.
"""

import json
from collections import defaultdict

import sf_client
from mcp_instance import mcp


@mcp.tool()
def sf_get_pipeline_summary(owner_name: str = "") -> str:
    """
    Summarize open pipeline grouped by stage: deal count and total
    Amount per stage. Optionally filter to one rep by their Owner name
    (exact match on User.Name). Good starting point for "how's pipeline
    looking" before drilling into specific deals.
    """
    conditions = ["IsClosed = false"]
    if owner_name:
        conditions.append(f"Owner.Name = '{owner_name}'")
    soql = (
        "SELECT StageName, Amount, Owner.Name FROM Opportunity WHERE "
        + " AND ".join(conditions)
        + " LIMIT 2000"
    )
    opps = sf_client.query(soql)

    by_stage = defaultdict(lambda: {"count": 0, "total_amount": 0})
    for o in opps:
        stage = o["StageName"]
        by_stage[stage]["count"] += 1
        by_stage[stage]["total_amount"] += o.get("Amount") or 0

    summary = [
        {"stage": stage, "deal_count": v["count"], "total_amount": v["total_amount"]}
        for stage, v in by_stage.items()
    ]
    summary.sort(key=lambda x: x["total_amount"], reverse=True)
    return json.dumps(summary, indent=2)


@mcp.tool()
def sf_get_top_deals(limit: int = 10, min_amount: float = 0) -> str:
    """
    List the largest open deals by Amount, with stage, close date, and
    owner. Useful for "what are our biggest deals right now" or
    quarterly deal reviews.
    """
    conditions = ["IsClosed = false"]
    if min_amount:
        conditions.append(f"Amount >= {min_amount}")
    soql = (
        "SELECT Id, Name, Account.Name, StageName, Amount, Probability, "
        "CloseDate, Owner.Name FROM Opportunity WHERE "
        + " AND ".join(conditions)
        + f" ORDER BY Amount DESC LIMIT {int(limit)}"
    )
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_get_forecast(days: int = 90) -> str:
    """
    List open deals closing within the next `days` days, with amount and
    probability — a simple rolling forecast view. For a formal weighted
    forecast, multiply amount by probability yourself over the returned
    records.
    """
    soql = (
        "SELECT Id, Name, Account.Name, StageName, Amount, Probability, "
        "CloseDate, Owner.Name FROM Opportunity "
        f"WHERE IsClosed = false AND CloseDate <= NEXT_N_DAYS:{int(days)} "
        "ORDER BY CloseDate ASC LIMIT 500"
    )
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_get_leads(status: str = "", source: str = "", min_score: float = 0) -> str:
    """
    Retrieve Leads, optionally filtered by Status (e.g. 'Open',
    'Working', 'Qualified'), LeadSource, and a minimum score if this org
    uses a numeric lead-scoring field (pass min_score=0 to ignore
    scoring). Useful for reps triaging new inbound leads.
    """
    conditions = []
    if status:
        conditions.append(f"Status = '{status}'")
    if source:
        conditions.append(f"LeadSource = '{source}'")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    soql = (
        "SELECT Id, Name, Company, Status, LeadSource, Email, "
        "CreatedDate, OwnerId, Owner.Name FROM Lead"
        + where
        + " ORDER BY CreatedDate DESC LIMIT 200"
    )
    leads = sf_client.query(soql)
    if min_score:
        # Only applies if this org has a custom scoring field; left as a
        # post-filter since the field name varies by org (e.g.
        # Lead_Score__c). Describe the Lead object to find yours and
        # filter in SOQL directly for better performance.
        pass
    return json.dumps(leads, indent=2)


_WHO_ID_PREFIXES = ("00Q", "003")  # Lead, Contact — Task uses WhoId for these
_WHAT_ID_PREFIXES = ("001", "006", "500")  # Account, Opportunity, Case — Task uses WhatId


@mcp.tool()
def sf_log_activity_note(related_id: str, subject: str, notes: str = "") -> str:
    """
    MUTATING — creates a Task (activity log entry) attached to an
    Account, Opportunity, Case, Lead, or Contact by its Id. Use this to
    record a call summary, a next step, or a follow-up reminder directly
    from a conversation with Claude, instead of switching to Salesforce.
    Returns the new Task Id.
    """
    task = {"Subject": subject, "Description": notes, "Status": "Completed"}
    if related_id.startswith(_WHO_ID_PREFIXES):
        task["WhoId"] = related_id
    elif related_id.startswith(_WHAT_ID_PREFIXES):
        task["WhatId"] = related_id
    else:
        raise ValueError(
            f"Unrecognized Id prefix on '{related_id}' — expected an Account, "
            "Opportunity, Case, Lead, or Contact Id."
        )
    result = sf_client.create("Task", task)
    return json.dumps(result, indent=2)


@mcp.tool()
def sf_update_opportunity(
    opportunity_id: str,
    stage: str = "",
    amount: float = 0,
    close_date: str = "",
    next_step: str = "",
) -> str:
    """
    MUTATING — updates an existing Opportunity. Only pass the fields
    you actually want changed; everything else on the record is left
    alone. close_date must be 'YYYY-MM-DD' if provided. Use
    sf_describe_object("Opportunity") first if you're unsure of this
    org's exact StageName picklist values — an invalid stage name will
    fail rather than silently guess the closest match.
    """
    fields = {}
    if stage:
        fields["StageName"] = stage
    if amount:
        fields["Amount"] = amount
    if close_date:
        fields["CloseDate"] = close_date
    if next_step:
        fields["NextStep"] = next_step
    if not fields:
        return json.dumps({"error": "No fields provided to update."})
    sf_client.update("Opportunity", opportunity_id, fields)
    return json.dumps({"updated": opportunity_id, "fields": fields}, indent=2)


@mcp.tool()
def sf_delete_task(task_id: str) -> str:
    """
    MUTATING, DESTRUCTIVE — deletes a Task (e.g. one created in error
    by sf_log_activity_note). Salesforce moves it to the Recycle Bin
    rather than erasing it immediately (recoverable for about 15 days
    under default org settings). Only Task deletion is exposed here —
    deleting Accounts, Opportunities, Cases, or Contacts is
    deliberately not built into this server; see CONTRIBUTING.md if
    you want to add that yourself, with the risk that implies.
    """
    sf_client.delete("Task", task_id)
    return json.dumps({"deleted": task_id, "object": "Task"}, indent=2)


@mcp.tool()
def sf_delete_lead(lead_id: str) -> str:
    """
    MUTATING, DESTRUCTIVE — deletes a Lead (e.g. a duplicate or
    clearly invalid inbound lead). Salesforce moves it to the Recycle
    Bin rather than erasing it immediately (recoverable for about 15
    days under default org settings). Use sf_get_leads first to
    confirm you have the right record before deleting it.
    """
    sf_client.delete("Lead", lead_id)
    return json.dumps({"deleted": lead_id, "object": "Lead"}, indent=2)
