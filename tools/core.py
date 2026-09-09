"""
Generic, object-agnostic tools. Every other module is really just a
documented, pre-built SOQL query on top of these — use sf_query directly
for anything not already covered by a domain module.
"""

import json

import sf_client
from mcp_instance import mcp


@mcp.tool()
def sf_list_objects() -> str:
    """
    List every Salesforce object (standard and custom) this connected
    user can query — Account, Opportunity, Case, Lead, Campaign, Contact,
    and any org-specific custom objects (names ending in __c). Call this
    first if you're not sure what data exists in this org.
    """
    return json.dumps(sf_client.list_objects(), indent=2)


@mcp.tool()
def sf_describe_object(object_name: str) -> str:
    """
    List the fields (and picklist values) on a Salesforce object, e.g.
    'Account', 'Opportunity', 'Case', 'Campaign', or a custom object like
    'Renewal_Risk__c'. Call this before writing a SOQL query against an
    object you haven't queried before, so field names are exact.
    """
    return json.dumps(sf_client.describe_object(object_name), indent=2)


@mcp.tool()
def sf_query(soql: str) -> str:
    """
    Run a raw SOQL query against this Salesforce org and return every
    matching record (pagination is handled automatically). Works across
    any object and any team's use case — Sales pipeline, PreSales deal
    detail, Marketing campaigns, or Customer Success renewals/cases.
    Example:
      "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity
       WHERE IsClosed = false ORDER BY CloseDate ASC LIMIT 50"
    Use sf_describe_object first if you're unsure of field names.
    """
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_get_open_opportunities(min_amount: float = 0, stage_contains: str = "") -> str:
    """
    Retrieve open (not closed) Opportunities with amount, stage,
    probability, and close date. Optionally filter by minimum Amount
    and/or a substring of the stage name (e.g. 'Negotiation', 'Technical
    Validation'). General-purpose enough for Sales, PreSales, or
    Customer Success renewal tracking — see tools/sales.py and
    tools/presales.py for more specific views on top of this.
    """
    conditions = ["IsClosed = false"]
    if min_amount:
        conditions.append(f"Amount >= {min_amount}")
    if stage_contains:
        conditions.append(f"StageName LIKE '%{stage_contains}%'")
    soql = (
        "SELECT Id, Name, AccountId, Account.Name, StageName, Amount, "
        "Probability, CloseDate, Description FROM Opportunity WHERE "
        + " AND ".join(conditions)
        + " ORDER BY CloseDate ASC LIMIT 200"
    )
    return json.dumps(sf_client.query(soql), indent=2)
