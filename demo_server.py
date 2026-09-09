#!/usr/bin/env python3
"""
Demo mode — try the tool shapes with fictional local data, no Salesforce
credentials required. Point Claude Desktop at this file instead of
server.py to explore before setting up a real org connection.

All company names in data/demo_salesforce.json are fictional.
"""

import json
from datetime import date
from pathlib import Path

from mcp.server.fastmcp import FastMCP

DATA_PATH = Path(__file__).parent / "data" / "demo_salesforce.json"
with open(DATA_PATH) as f:
    SF_DATA = json.load(f)

mcp = FastMCP("salesforce-cloud-demo")


@mcp.tool()
def sf_get_accounts(segment: str = "", min_arr: float = 0) -> str:
    """
    [DEMO DATA] Retrieve fictional customer accounts with ARR, health
    score, CSM owner, renewal date, and segment. Optionally filter by
    segment ('Strategic' or 'Growth') and minimum ARR.
    """
    accounts = SF_DATA["accounts"]
    if segment:
        accounts = [a for a in accounts if a["segment"] == segment]
    if min_arr:
        accounts = [a for a in accounts if a["arr"] >= min_arr]
    return json.dumps(accounts, indent=2)


@mcp.tool()
def sf_get_open_opportunities(stage_filter: str = "") -> str:
    """
    [DEMO DATA] Retrieve fictional open renewal/expansion opportunities
    with stage, probability, close date, and notes. Filter by stage.
    """
    opps = SF_DATA["opportunities"]
    if stage_filter:
        opps = [o for o in opps if o["stage"] == stage_filter]
    return json.dumps(opps, indent=2)


@mcp.tool()
def sf_get_open_cases(account_id: str = "", severity: str = "") -> str:
    """
    [DEMO DATA] Retrieve fictional open support cases by account,
    including severity, days open, and description.
    """
    cases = SF_DATA["cases"]
    if account_id:
        cases = [c for c in cases if c["account_id"] == account_id]
    if severity:
        cases = [c for c in cases if c["severity"] == severity]
    return json.dumps(cases, indent=2)


@mcp.tool()
def sf_get_at_risk_accounts(days_to_renewal: int = 90, health_score_threshold: int = 60) -> str:
    """
    [DEMO DATA] Fictional churn-risk view combining low health score,
    upcoming renewals, negative call sentiment, open critical cases, and
    contact gaps. Mirrors what sf_get_at_risk_accounts does against a
    real org in server.py, using standard fields instead.
    """
    today = date.today()
    at_risk = []

    for account in SF_DATA["accounts"]:
        flags = []
        renewal = date.fromisoformat(account["renewal_date"])
        days_until_renewal = (renewal - today).days

        if account["health_score"] < health_score_threshold:
            flags.append(f"Low health score: {account['health_score']}/100")
        if days_until_renewal <= days_to_renewal:
            flags.append(f"Renewal in {days_until_renewal} days ({account['renewal_date']})")
        if account["last_csm_contact_days"] > 21:
            flags.append(f"No CSM contact in {account['last_csm_contact_days']} days")
        if account["nps"] <= 6:
            flags.append(f"Low NPS: {account['nps']}")

        account_cases = [
            c for c in SF_DATA["cases"]
            if c["account_id"] == account["id"]
            and c["severity"] in ("Critical", "High")
            and c["status"] == "Open"
        ]
        if account_cases:
            flags.append(f"{len(account_cases)} open Critical/High case(s)")

        account_gong = [
            g for g in SF_DATA["gong_notes"]
            if g["account_id"] == account["id"]
            and g["sentiment"] == "Negative"
        ]
        if account_gong:
            flags.append("Negative call sentiment on last call")

        if flags:
            at_risk.append({
                "account_id": account["id"],
                "account_name": account["name"],
                "arr": account["arr"],
                "segment": account["segment"],
                "csm": account["csm"],
                "health_score": account["health_score"],
                "renewal_date": account["renewal_date"],
                "risk_flags": flags,
            })

    at_risk.sort(key=lambda x: x["arr"], reverse=True)
    return json.dumps(at_risk, indent=2)


if __name__ == "__main__":
    mcp.run()
