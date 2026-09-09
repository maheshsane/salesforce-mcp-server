"""
Customer Success tools — support load, renewals, and churn-risk
signals, built on standard Case/Opportunity fields.
"""

import json
from collections import defaultdict

import sf_client
from mcp_instance import mcp


@mcp.tool()
def sf_get_open_cases(priority: str = "") -> str:
    """
    Retrieve open support Cases with priority, subject, and account.
    Optionally filter by Priority ('High', 'Medium', 'Low', or whatever
    values this org's picklist uses — check with sf_describe_object if
    unsure).
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
def sf_get_renewals_due(days: int = 90) -> str:
    """
    List open Opportunities closing within the next `days` days —
    treat as upcoming renewals if this org uses Opportunities for
    renewal tracking (common pattern; if your org uses a different
    object or a custom renewal date field, describe that object and use
    sf_query instead).
    """
    soql = (
        "SELECT Id, Name, Account.Name, Amount, StageName, Probability, "
        "CloseDate, Owner.Name FROM Opportunity "
        f"WHERE IsClosed = false AND CloseDate <= NEXT_N_DAYS:{int(days)} "
        "ORDER BY CloseDate ASC LIMIT 200"
    )
    return json.dumps(sf_client.query(soql), indent=2)


@mcp.tool()
def sf_get_at_risk_accounts(days_to_close: int = 90, max_probability: int = 50) -> str:
    """
    Best-effort churn/risk view built from standard objects: open
    Opportunities closing within `days_to_close` days with probability
    at or below `max_probability`, cross-referenced with any open
    High/Critical-priority Cases on the same account. Ranked by deal
    amount. If this org tracks health score, NPS, or renewal risk on a
    custom field, describe that object and query it directly instead —
    this tool only knows about standard fields.
    """
    opp_soql = (
        "SELECT Id, Name, AccountId, Account.Name, Amount, Probability, "
        "CloseDate, StageName FROM Opportunity "
        f"WHERE IsClosed = false AND Probability <= {max_probability} "
        f"AND CloseDate <= NEXT_N_DAYS:{days_to_close} ORDER BY Amount DESC LIMIT 200"
    )
    at_risk_opps = sf_client.query(opp_soql)

    case_soql = (
        "SELECT AccountId, Id FROM Case WHERE IsClosed = false "
        "AND Priority IN ('High', 'Critical') LIMIT 500"
    )
    risky_cases = sf_client.query(case_soql)
    cases_by_account = defaultdict(int)
    for c in risky_cases:
        cases_by_account[c["AccountId"]] += 1

    results = []
    for opp in at_risk_opps:
        flags = [f"Closing in {opp['CloseDate']} at {opp['Probability']}% probability"]
        open_cases = cases_by_account.get(opp["AccountId"], 0)
        if open_cases:
            flags.append(f"{open_cases} open High/Critical case(s)")
        results.append({
            "account_id": opp["AccountId"],
            "account_name": (opp.get("Account") or {}).get("Name"),
            "opportunity": opp["Name"],
            "amount": opp["Amount"],
            "stage": opp["StageName"],
            "risk_flags": flags,
        })
    return json.dumps(results, indent=2)


@mcp.tool()
def sf_get_account_health(account_id: str) -> str:
    """
    Quick health snapshot for one Account: open case count by priority,
    days since last logged activity, and any open (not-yet-closed)
    Opportunities. Lighter-weight than sf_get_account_360 — use this
    when you just need a fast health check, not the full picture.
    """
    cases = sf_client.query(
        "SELECT Priority FROM Case WHERE IsClosed = false "
        f"AND AccountId = '{account_id}' LIMIT 200"
    )
    cases_by_priority = defaultdict(int)
    for c in cases:
        cases_by_priority[c.get("Priority") or "Unspecified"] += 1

    last_activity = sf_client.query(
        "SELECT ActivityDate FROM Task WHERE AccountId = "
        f"'{account_id}' ORDER BY ActivityDate DESC LIMIT 1"
    )
    open_opps = sf_client.query(
        "SELECT Id, Name, StageName, Amount, CloseDate FROM Opportunity "
        f"WHERE AccountId = '{account_id}' AND IsClosed = false LIMIT 50"
    )

    return json.dumps({
        "account_id": account_id,
        "open_cases_by_priority": dict(cases_by_priority),
        "last_activity_date": last_activity[0]["ActivityDate"] if last_activity else None,
        "open_opportunities": open_opps,
    }, indent=2)
