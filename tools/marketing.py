"""
Marketing tools built on core Salesforce's Campaign object — this is
distinct from the Marketing Cloud product (see tools/marketing_cloud.py
and mc_client.py), but it's where campaign ROI and lead attribution
usually live if this org uses standard Salesforce campaigns rather than,
or alongside, Marketing Cloud Journeys.
"""

import json

import sf_client
from mcp_instance import mcp


@mcp.tool()
def sf_get_campaigns(status: str = "", campaign_type: str = "") -> str:
    """
    List Campaigns with their standard performance rollups: leads
    generated, converted leads, opportunities generated, won
    opportunities, amount won, expected revenue, and actual cost.
    Optionally filter by Status (e.g. 'In Progress', 'Completed') or
    Type (e.g. 'Webinar', 'Email', 'Conference').
    """
    conditions = []
    if status:
        conditions.append(f"Status = '{status}'")
    if campaign_type:
        conditions.append(f"Type = '{campaign_type}'")
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    soql = (
        "SELECT Id, Name, Status, Type, StartDate, EndDate, "
        "NumberOfLeads, NumberOfConvertedLeads, NumberOfOpportunities, "
        "NumberOfWonOpportunities, AmountWonOpportunities, ExpectedRevenue, "
        "ActualCost, BudgetedCost FROM Campaign"
        + where
        + " ORDER BY StartDate DESC LIMIT 200"
    )
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_get_campaign_performance(campaign_id: str) -> str:
    """
    ROI detail for one Campaign: cost vs. amount won, plus a simple
    cost-per-lead and cost-per-opportunity calculation. Use this to
    answer "did this campaign pay for itself."
    """
    soql = (
        "SELECT Id, Name, ActualCost, BudgetedCost, NumberOfLeads, "
        "NumberOfConvertedLeads, NumberOfOpportunities, NumberOfWonOpportunities, "
        f"AmountWonOpportunities, ExpectedRevenue FROM Campaign WHERE Id = '{campaign_id}'"
    )
    result = sf_client.query(soql)
    if not result:
        return json.dumps({"error": f"No Campaign found with Id {campaign_id}"})

    c = result[0]
    cost = c.get("ActualCost") or 0
    leads = c.get("NumberOfLeads") or 0
    opps = c.get("NumberOfOpportunities") or 0
    c["cost_per_lead"] = round(cost / leads, 2) if leads else None
    c["cost_per_opportunity"] = round(cost / opps, 2) if opps else None
    c["roi_multiple"] = round((c.get("AmountWonOpportunities") or 0) / cost, 2) if cost else None
    return json.dumps(c, indent=2)


@mcp.tool()
def sf_get_campaign_members(campaign_id: str, status: str = "") -> str:
    """
    List the Leads/Contacts attached to a Campaign, with their member
    status (e.g. 'Sent', 'Responded', 'Registered', 'Attended' — exact
    values depend on this org's campaign member status picklist).
    Optionally filter to one status.
    """
    conditions = [f"CampaignId = '{campaign_id}'"]
    if status:
        conditions.append(f"Status = '{status}'")
    soql = (
        "SELECT Id, Name, Email, Status, Lead.Status, Contact.Title "
        "FROM CampaignMember WHERE "
        + " AND ".join(conditions)
        + " LIMIT 500"
    )
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_get_leads_by_source(days: int = 90) -> str:
    """
    Group Leads created in the last `days` days by LeadSource, with
    total count and converted count per source — a quick top-of-funnel
    view of which channels are producing (and converting) leads.
    """
    soql = (
        "SELECT LeadSource, IsConverted FROM Lead "
        f"WHERE CreatedDate = LAST_N_DAYS:{int(days)} LIMIT 5000"
    )
    leads = sf_client.query(soql)

    from collections import defaultdict
    by_source = defaultdict(lambda: {"total": 0, "converted": 0})
    for lead in leads:
        source = lead.get("LeadSource") or "Unspecified"
        by_source[source]["total"] += 1
        if lead.get("IsConverted"):
            by_source[source]["converted"] += 1

    summary = [
        {"source": source, "total_leads": v["total"], "converted_leads": v["converted"]}
        for source, v in by_source.items()
    ]
    summary.sort(key=lambda x: x["total_leads"], reverse=True)
    return json.dumps(summary, indent=2)
