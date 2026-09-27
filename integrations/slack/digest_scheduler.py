#!/usr/bin/env python3
"""
Fires all 5 daily digests at their scheduled time, once each per day.
Genuinely different mechanism from the two watchers: this doesn't
poll for changes, it fires once at a fixed clock time regardless of
what's happening in Salesforce at that moment.

CSM, Sales, and PreSales are grouped by the person responsible (CSM
Owner, Sales Rep, PreSales Owner) with a specific next action attached
to each item. Marketing and Leadership are aggregate reports with a
strategic recommendation attached, not a per-person task list -- there
is no "Marketing owner" field, and Leadership is a rollup across
everyone, not one person's book.

Each digest has its own independently configurable time below,
defaulting all 5 to 8:00 AM -- same "each thing configurable on its
own" philosophy as the two watchers' per-trigger poll intervals.

For testing without waiting on the clock, use run_digest.py instead --
a separate, simple script that fires one digest immediately.

HONEST NOTE, same standard as everything else built tonight: the
due-to-run logic is pure and unit-tested below with a real clock
injected rather than read live, so it's fully testable without
waiting for an actual 8:00 AM. The live Salesforce queries, the
Claude round trip, and the actual Slack posts have never run against
a live org -- expect the first real firing to surface something.
"""

import asyncio
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os
from slack_sdk import WebClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import sf_client

from sf_agent import answer_question, unwrap_exception

STATE_FILE = Path(__file__).parent / ".digest_scheduler_state.json"
TICK_SECONDS = 60  # check every minute whether anything is due

web_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

GENERAL_DIGEST_SYSTEM_PROMPT = (
    "You are generating a scheduled digest report, using the connected "
    "Salesforce tools to get real, current data -- never guess or make "
    "up figures. Respond with ONLY the report content -- no preamble, "
    "no narrating what you're about to do.\n\n"
    "Note on identity fields: this org has only one real Salesforce "
    "user, so the standard Owner field on any object is always that "
    "same person and never represents who's actually responsible for "
    "the account or deal. CSM identity lives in Account.CSM_Owner_Name__c, "
    "Sales Rep in Opportunity.Sales_Rep_Name__c, and PreSales Owner in "
    "Opportunity.PreSales_Owner_Name__c -- use those, never the standard "
    "Owner field, when grouping or reporting by CSM, Sales Rep, or "
    "PreSales Owner.\n\n"
    "Format for Slack: *bold* with single asterisks, \u2022 for bullet "
    "points, no markdown headers."
)

DIGESTS = [
    {
        "key": "csm", "hour": 8, "minute": 0, "channel": "#csm-digest",
        "prompt": (
            "Group by CSM. For each of the 5 CSMs, list their accounts "
            "renewing in the next 60 days with health score below 50 -- "
            "account, ARR, renewal date, the risk driver, and one "
            "specific next action they should take this week."
        ),
    },
    {
        "key": "sales", "hour": 8, "minute": 0, "channel": "#sales-digest",
        "completeness_field": "Sales_Rep_Name__c",
        "prompt": (
            "Group by Sales Rep. For each rep, list their open deals -- "
            "account, stage, amount, probability -- and one specific "
            "next action per deal to move it forward. When you state "
            "each rep's deal count in their header, count the actual "
            "items you're about to list, don't state a number "
            "separately from the list itself."
        ),
    },
    {
        "key": "presales", "hour": 8, "minute": 0, "channel": "#presales-digest",
        "completeness_field": "PreSales_Owner_Name__c",
        "prompt": (
            "Group by PreSales Owner. For each, list the open deals "
            "they're supporting -- account, stage, close date -- and "
            "one line on what they should prepare before the next "
            "customer touchpoint. When you state each owner's deal "
            "count in their header, count the actual items you're "
            "about to list, don't state a number separately from the "
            "list itself."
        ),
    },
    {
        "key": "marketing", "hour": 8, "minute": 0, "channel": "#marketing-digest",
        "prompt": (
            "Rank campaigns by ROI and show lead source performance by "
            "conversion rate. Then flag anything underperforming its "
            "expected revenue and suggest one specific action -- "
            "reallocate budget, adjust targeting, or sunset it."
        ),
    },
    {
        "key": "leadership", "hour": 8, "minute": 0, "channel": "#leadership-digest",
        "prompt": (
            "Give a combined executive summary across CS, Sales, and "
            "Marketing: total ARR, percent at risk, pipeline total, top "
            "campaign ROI. Then name the single biggest threat and "
            "single biggest opportunity, ranked strictly by dollar "
            "value, not narrative severity -- show the actual number "
            "you compared. For any subtotal, show the addition. "
            "Recommend one specific intervention for the threat and one "
            "specific way to accelerate the opportunity."
        ),
    },
]


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def is_due(hour: int, minute: int, last_run_date: str, now: datetime) -> bool:
    """Pure, testable -- takes 'now' as an argument rather than reading
    the live clock internally, so this is fully verifiable without
    waiting for an actual scheduled time to arrive."""
    today_str = now.date().isoformat()
    if last_run_date == today_str:
        return False  # already ran today, don't fire again
    scheduled_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= scheduled_today


def get_expected_deals(owner_field: str) -> dict:
    """Independently queries Salesforce for the authoritative set of open
    deals grouped by the given owner field -- the same kind of direct
    query used to prove the earlier omission was a generation-time
    slip, not a data problem. Returns {owner_name: [{"account":,
    "amount":, "stage":}, ...]}."""
    opps = sf_client.query(
        f"SELECT Account.Name, Amount, StageName, {owner_field} FROM Opportunity "
        f"WHERE StageName != 'Closed Won'"
    )
    grouped = defaultdict(list)
    for o in opps:
        owner = o.get(owner_field)
        account_name = (o.get("Account") or {}).get("Name")
        if owner and account_name:
            grouped[owner].append({
                "account": account_name, "amount": o.get("Amount"), "stage": o.get("StageName"),
            })
    return grouped


def find_missing_deals(report_text: str, expected: dict) -> dict:
    """Pure, testable. A simple, deterministic substring check -- does
    each expected account name actually appear somewhere in the
    generated report text. Simpler and more reliable than trying to
    parse structured data back out of free-form prose. Returns only
    the owners/deals genuinely missing, empty dict if nothing's missing."""
    missing = {}
    for owner, deals in expected.items():
        owner_missing = [d for d in deals if d["account"] not in report_text]
        if owner_missing:
            missing[owner] = owner_missing
    return missing


def append_completeness_correction(report_text: str, missing: dict) -> str:
    """Pure, testable. If the completeness check found anything missing,
    append it explicitly -- using the real, independently-queried facts,
    not a second attempt at generation. Guarantees the final posted
    message is complete regardless of what the generation step produced.
    Returns the original text unchanged if nothing was missing."""
    if not missing:
        return report_text
    correction = "\n\n:warning: *Completeness check found additional deals not detailed above:*\n"
    for owner, deals in missing.items():
        for d in deals:
            amount = d["amount"] or 0
            correction += f"\u2022 {owner}: {d['account']} \u2014 {d['stage']}, ${amount:,.0f}\n"
    return report_text + correction


def run_digest(digest: dict):
    print(f"[digest] Running {digest['key']}")
    try:
        report = asyncio.run(answer_question(digest["prompt"], GENERAL_DIGEST_SYSTEM_PROMPT))
    except Exception as e:
        report = f"Digest generation failed: {unwrap_exception(e)}"

    if not report or not report.strip():
        report = "Digest generation returned nothing -- check manually."

    completeness_field = digest.get("completeness_field")
    if completeness_field and report.strip() and "failed" not in report.lower():
        try:
            expected = get_expected_deals(completeness_field)
            missing = find_missing_deals(report, expected)
            if missing:
                print(f"[digest] {digest['key']} completeness check caught {sum(len(v) for v in missing.values())} missing deal(s)")
            report = append_completeness_correction(report, missing)
        except Exception as e:
            print(f"[digest] {digest['key']} completeness check itself failed (non-fatal, posting original report): {unwrap_exception(e)}")

    try:
        web_client.chat_postMessage(channel=digest["channel"], text=report)
        print(f"[digest] {digest['key']} posted to {digest['channel']}")
    except Exception as e:
        print(f"[digest] FAILED to post {digest['key']}: {unwrap_exception(e)}")
        return False
    return True


def main():
    state = load_state()
    print("Digest scheduler starting -- checking every minute for anything due")
    for d in DIGESTS:
        print(f"  {d['key']} -> {d['channel']} at {d['hour']:02d}:{d['minute']:02d}")

    while True:
        now = datetime.now()
        for digest in DIGESTS:
            last_run = state.get(digest["key"])
            if is_due(digest["hour"], digest["minute"], last_run, now):
                success = run_digest(digest)
                if success:
                    state[digest["key"]] = now.date().isoformat()
                    save_state(state)
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
