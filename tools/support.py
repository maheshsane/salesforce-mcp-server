"""
Support tools — case intake, triage, and resolution. Split out from
Customer Success deliberately: Support (working the ticket queue,
answering "what's open and how urgent") and Customer Success (working
the relationship, answering "who's about to churn") are different
jobs in most orgs, even though they share the same Case object.
"""

import json

import sf_client
from mcp_instance import mcp


@mcp.tool()
def sf_get_open_cases(priority: str = "") -> str:
    """
    Retrieve open support Cases with priority, subject, and account.
    Optionally filter by Priority ('High', 'Medium', 'Low', or whatever
    values this org's picklist uses — check with sf_describe_object if
    unsure). The queue-check tool: what's open right now.
    """
    conditions = ["IsClosed = false"]
    if priority:
        conditions.append(f"Priority = '{priority}'")
    soql = (
        "SELECT Id, CaseNumber, Subject, AccountId, Account.Name, Priority, "
        "Status, CreatedDate, Description FROM Case WHERE "
        + " AND ".join(conditions)
        + " ORDER BY CreatedDate ASC LIMIT 200"
    )
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_create_case(account_id: str, subject: str, description: str = "", priority: str = "Medium") -> str:
    """
    MUTATING — creates a new support Case on an Account. Check this
    org's actual Priority picklist values with
    sf_describe_object("Case") first if unsure — the default 'Medium'
    assumes standard Salesforce values, which some orgs customize.
    Returns the new Case Id and Case Number.
    """
    case = {
        "AccountId": account_id,
        "Subject": subject,
        "Description": description,
        "Priority": priority,
    }
    result = sf_client.create("Case", case)
    return json.dumps(result, indent=2)


@mcp.tool()
def sf_update_case(case_id: str, status: str = "", priority: str = "", description: str = "") -> str:
    """
    MUTATING — updates an existing Case: resolve it, escalate its
    priority, or add detail. Only pass the fields you actually want
    changed. Check sf_describe_object("Case") first if unsure of this
    org's exact Status/Priority picklist values.
    """
    fields = {}
    if status:
        fields["Status"] = status
    if priority:
        fields["Priority"] = priority
    if description:
        fields["Description"] = description
    if not fields:
        return json.dumps({"error": "No fields provided to update."})
    sf_client.update("Case", case_id, fields)
    return json.dumps({"updated": case_id, "fields": fields}, indent=2)
