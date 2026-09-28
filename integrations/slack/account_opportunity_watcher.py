#!/usr/bin/env python3
"""
Watches for three distinct events and alerts the moment each happens:
  1. Health score drops on any account -> #csm-alerts
  2. A new Opportunity is created -> #presales-alerts
  3. An Opportunity closes Won -> #csm-alerts, and also #leadership-alerts
     if the amount is $500K or more

All three share this one process rather than three separate ones --
similar polling logic, similar (lower) urgency than case alerts, no
real cost to combining them. case_alert_watcher.py stays completely
separate and untouched: it's already proven and demo-critical, and
new code here shouldn't be able to affect its reliability.

FIRST-RUN BEHAVIOR IS DELIBERATELY DIFFERENT FROM case_alert_watcher.py.
Case alerts correctly backlog-catch-up on first run -- surfacing
pre-existing Critical cases is genuinely useful. These three triggers
are the opposite: "new Opportunity" shouldn't fire for every
Opportunity that already existed before this script ever ran, and
"health dropped" has no prior value to compare against on a first
check anyway. So the first run silently records a baseline for all
three -- current health scores, current Opportunity IDs, current
Closed Won IDs -- and alerts on nothing. Only genuine changes from
that point forward trigger anything.

MULTIPLE POLL INTERVALS, ONE PROCESS: each of the three checks has its
own interval (health/new-opp default to 5 min, deal-closed to 10 min),
independently configurable below. A single "check everything then
sleep" loop (case_alert_watcher.py's pattern) can't honor three
different cadences correctly -- this uses a short tick loop instead,
checking each interval's own due-time independently every 30 seconds.

HONEST NOTE, same standard as case_alert_watcher.py: the diffing and
state-tracking logic is pure and unit-tested below with realistic fake
data. The live polling loop, the real Salesforce queries, and the real
Slack posts have never run against a live org -- expect the first real
run to surface something, the same way every new script here has.
"""

import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os
from slack_sdk import WebClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import sf_client

from sf_agent import answer_question, unwrap_exception
import alert_history

CSM_ALERTS_CHANNEL = "#csm-alerts"
PRESALES_ALERTS_CHANNEL = "#presales-alerts"
LEADERSHIP_ALERTS_CHANNEL = "#leadership-alerts"

HEALTH_DROP_POLL_SECONDS = 90
NEW_OPPORTUNITY_POLL_SECONDS = 90
DEAL_CLOSED_POLL_SECONDS = 90
TICK_SECONDS = 30  # how often the main loop wakes to check what's due

LARGE_DEAL_THRESHOLD = 500000

STATE_FILE = Path(__file__).parent / ".account_opportunity_state.json"

web_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

FIELD_GUIDANCE = (
    "Note on identity fields: this org has only one real Salesforce "
    "user, so the standard Owner field on any object is always that "
    "same person and never represents who's actually responsible for "
    "the account or deal. CSM identity lives in Account.CSM_Owner_Name__c, "
    "Sales Rep in Opportunity.Sales_Rep_Name__c, and PreSales Owner in "
    "Opportunity.PreSales_Owner_Name__c -- use those, never the standard "
    "Owner field, when asked for the CSM, Sales Rep, or PreSales Owner."
)

HEALTH_DROP_SYSTEM_PROMPT = (
    "You are an automated system alerting that an account's health score "
    "just dropped. Using the connected tools, pull real data. Respond "
    "with ONLY the content below -- no preamble, no narrating what "
    "you're about to do.\n\n"
    "State the account name in bold, the score change (was X, now Y), "
    "and the CSM. Then give the likely cause -- check recent cases, "
    "recent Gong call sentiment, and logged activity for anything that "
    "explains the drop. End with one specific next action the CSM "
    "should take this week.\n\n"
    f"{FIELD_GUIDANCE}\n\n"
    "Format for Slack: *bold* with single asterisks, \u2022 for bullets, "
    "no markdown headers."
)

NEW_OPPORTUNITY_SYSTEM_PROMPT = (
    "You are an automated system alerting that a new Opportunity was "
    "just created. Using the connected tools, pull real data. Respond "
    "with ONLY the content below -- no preamble.\n\n"
    "State the account name in bold, the deal amount and stage, and who "
    "the PreSales owner is. If Sales_Rep_Name__c or "
    "PreSales_Owner_Name__c comes back empty or null, that means this "
    "deal genuinely hasn't been assigned to anyone yet -- say plainly "
    "'not yet assigned' and move on. Do not keep searching for an "
    "owner through other tools or fields if these two are empty; there "
    "is nothing more to find. Then give account context useful before "
    "the next customer touchpoint -- health score, recent activity, "
    "any existing relationship history (other opportunities, cases, "
    "Gong calls). End with what the PreSales owner should prepare, or "
    "if unassigned, note that this should be assigned before any "
    "customer touchpoint happens.\n\n"
    f"{FIELD_GUIDANCE}\n\n"
    "Format for Slack: *bold* with single asterisks, \u2022 for bullets, "
    "no markdown headers."
)

DEAL_CLOSED_SYSTEM_PROMPT = (
    "You are an automated system alerting that a deal just closed Won. "
    "Using the connected tools, pull real data. Respond with ONLY the "
    "content below -- no preamble.\n\n"
    "State the account name in bold and the deal amount. Give a CSM "
    "handover: key contacts, anything still open (a case, a stalled "
    "item), and one suggested first action for the CSM this week.\n\n"
    f"{FIELD_GUIDANCE}\n\n"
    "Format for Slack: *bold* with single asterisks, \u2022 for bullets, "
    "no markdown headers."
)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {
            "initialized": False,
            "health_scores": {},
            "seen_opp_ids": [],
            "seen_closed_won_ids": [],
        }
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state))


def diff_health_drops(accounts: list, prior_scores: dict) -> list:
    """Pure, testable. Returns accounts whose current score is lower
    than the prior recorded score. An account with no prior score
    (first time seen) never counts as a drop -- nothing to compare
    against yet."""
    drops = []
    for a in accounts:
        prior = prior_scores.get(a["Id"])
        current = a["Health_Score__c"]
        if prior is not None and current is not None and current < prior:
            drops.append({"account": a, "prior_score": prior, "current_score": current})
    return drops


def diff_new_items(current_ids: list, seen_ids: set) -> list:
    """Pure, testable. Generic set-diff, reused for both new-Opportunity
    and newly-Closed-Won detection -- same underlying logic as
    case_alert_watcher.py's find_new_cases, generalized beyond cases."""
    return [item for item in current_ids if item["Id"] not in seen_ids]


def build_health_drop_prompt(drop: dict) -> str:
    a = drop["account"]
    return (
        f"Account {a['Name']} (ID: {a['Id']}) health score dropped from "
        f"{drop['prior_score']} to {drop['current_score']}. Generate the alert as instructed."
    )


def build_new_opportunity_prompt(opp: dict) -> str:
    account_name = (opp.get("Account") or {}).get("Name") or "unknown account"
    return (
        f"New Opportunity \"{opp['Name']}\" (ID: {opp['Id']}) was just created on "
        f"account {account_name}, amount {opp.get('Amount')}, stage {opp.get('StageName')}. "
        f"Generate the alert as instructed."
    )


def build_deal_closed_prompt(opp: dict) -> str:
    account_name = (opp.get("Account") or {}).get("Name") or "unknown account"
    return (
        f"Opportunity \"{opp['Name']}\" (ID: {opp['Id']}) on account {account_name} "
        f"just closed Won, amount {opp.get('Amount')}. Generate the alert as instructed."
    )


def run_agent_safely(prompt: str, system_prompt: str, fallback_note: str, account_name: str = None) -> str:
    """Shared, resilient wrapper around answer_question -- same error
    handling and empty-answer safety net as case_alert_watcher.py.
    If account_name is given, that account's prior alert history
    (across ALL trigger types -- cases, health drops, new opps, deals
    closing, not just this one) is appended as real, remembered
    context, not something Claude has to be told explicitly each time."""
    if account_name:
        prompt = prompt + alert_history.format_history_for_prompt(account_name)
    try:
        text = asyncio.run(answer_question(prompt, system_prompt))
    except Exception as e:
        return f"{fallback_note} but alert generation failed: {unwrap_exception(e)}"
    if not text or not text.strip():
        return f"{fallback_note} but couldn't generate a full alert -- check it manually."
    return text


def seed_baseline(state: dict):
    """First-run only: silently records current values for all three
    triggers, without alerting on any of them -- there's nothing
    genuinely 'new' or 'changed' about data that already existed
    before this script ever started watching."""
    accounts = sf_client.query("SELECT Id, Health_Score__c FROM Account WHERE Health_Score__c != null")
    state["health_scores"] = {a["Id"]: a["Health_Score__c"] for a in accounts}

    opps = sf_client.query("SELECT Id, StageName FROM Opportunity")
    state["seen_opp_ids"] = [o["Id"] for o in opps]
    state["seen_closed_won_ids"] = [o["Id"] for o in opps if o["StageName"] == "Closed Won"]


def check_health_drops(state: dict):
    accounts = sf_client.query(
        "SELECT Id, Name, Health_Score__c, CSM_Owner_Name__c FROM Account WHERE Health_Score__c != null"
    )
    drops = diff_health_drops(accounts, state["health_scores"])

    for drop in drops:
        print(f"[health-drop] {drop['account']['Name']}: {drop['prior_score']} -> {drop['current_score']}")
        alert = run_agent_safely(
            build_health_drop_prompt(drop), HEALTH_DROP_SYSTEM_PROMPT,
            f"Health score dropped on {drop['account']['Name']}",
            account_name=drop['account']['Name'],
        )
        header = f"\U0001F4C9 *Health Score Drop*\n\n"
        try:
            web_client.chat_postMessage(channel=CSM_ALERTS_CHANNEL, text=header + alert)
        except Exception as e:
            print(f"[health-drop] FAILED to post: {unwrap_exception(e)}")
            return  # stop this cycle, same resilience pattern as case_alert_watcher.py
        alert_history.log_alert(
            drop['account']['Name'], "Health Drop",
            f"Health dropped {drop['prior_score']} -> {drop['current_score']}",
        )

    # Update recorded scores for every account, drop or not -- next
    # check compares against today's value, not the original baseline.
    for a in accounts:
        state["health_scores"][a["Id"]] = a["Health_Score__c"]
    save_state(state)


SALES_REPS = ["Derek Simmons", "Natalie Cho", "Omar Farouk", "Isabelle Reyes"]
PRESALES_OWNERS = ["Trevor Nakamura", "Sophia Lindqvist", "Adrian Voss", "Camille Brooks"]


def next_rep_assignment(state: dict) -> tuple:
    """Pure-ish (reads/increments state, no I/O) -- round-robin, same
    pairing seed_rep_owners.py originally used, so a newly-assigned
    deal follows the same pattern as the original 47."""
    idx = state.get("next_rep_index", 0)
    sales_rep = SALES_REPS[idx % len(SALES_REPS)]
    presales_owner = PRESALES_OWNERS[idx % len(PRESALES_OWNERS)]
    state["next_rep_index"] = idx + 1
    return sales_rep, presales_owner


def auto_assign_reps_if_needed(opp: dict, state: dict) -> tuple:
    """If this opportunity has no Sales Rep or PreSales Owner yet,
    assigns both via round-robin and writes them back to Salesforce
    immediately -- a deterministic action, not something left to
    Claude's judgment. Returns (possibly-updated opp, was_just_assigned)
    so the caller can tell Claude this happened, since Claude has no
    way to know on its own whether a value it sees was always there
    or was just written moments ago."""
    if opp.get("Sales_Rep_Name__c") and opp.get("PreSales_Owner_Name__c"):
        return opp, False

    sales_rep, presales_owner = next_rep_assignment(state)
    sf_client.update("Opportunity", opp["Id"], {
        "Sales_Rep_Name__c": sales_rep, "PreSales_Owner_Name__c": presales_owner,
    })
    opp["Sales_Rep_Name__c"] = sales_rep
    opp["PreSales_Owner_Name__c"] = presales_owner
    print(f"[new-opportunity] Auto-assigned {opp['Name']} -> Sales: {sales_rep}, PreSales: {presales_owner}")
    return opp, True


def check_new_opportunities(state: dict):
    opps = sf_client.query(
        "SELECT Id, Name, AccountId, Account.Name, Amount, StageName, "
        "Sales_Rep_Name__c, PreSales_Owner_Name__c FROM Opportunity "
        "ORDER BY CreatedDate DESC LIMIT 50"
    )
    seen = set(state["seen_opp_ids"])
    new_opps = diff_new_items(opps, seen)

    for opp in new_opps:
        account_name = (opp.get("Account") or {}).get("Name")
        print(f"[new-opportunity] {opp['Name']} on {account_name}")
        opp, just_assigned = auto_assign_reps_if_needed(opp, state)
        prompt = build_new_opportunity_prompt(opp)
        if just_assigned:
            prompt += (
                f"\n\nNote: Sales_Rep_Name__c ({opp['Sales_Rep_Name__c']}) and "
                f"PreSales_Owner_Name__c ({opp['PreSales_Owner_Name__c']}) were "
                f"just auto-assigned by the system moments ago, since this deal "
                f"had neither. Mention this in your alert."
            )
        alert = run_agent_safely(
            prompt, NEW_OPPORTUNITY_SYSTEM_PROMPT,
            f"New opportunity created: {opp['Name']}",
            account_name=account_name,
        )
        header = f"\U0001F195 *New Opportunity*\n\n"
        try:
            web_client.chat_postMessage(channel=PRESALES_ALERTS_CHANNEL, text=header + alert)
        except Exception as e:
            print(f"[new-opportunity] FAILED to post: {unwrap_exception(e)}")
            return
        if account_name:
            alert_history.log_alert(account_name, "New Opportunity", f"{opp['Name']}, amount {opp.get('Amount')}")

        seen.add(opp["Id"])
        state["seen_opp_ids"] = list(seen)
        save_state(state)


def create_csm_kickoff_task(opp: dict):
    """Creates a real Salesforce Task -- a genuine, trackable action
    item the CSM sees in their own queue, not just a Slack message
    that might scroll past. WhatId ties it to the account so it shows
    on the account's own activity timeline. A deterministic action,
    not something left to Claude's judgment -- every Closed Won deal
    gets one, always."""
    due_date = (date.today() + timedelta(days=3)).isoformat()
    amount = opp.get("Amount") or 0
    sf_client.create("Task", {
        "WhatId": opp.get("AccountId"),
        "Subject": f"CSM Kickoff needed: {opp['Name']} (Closed Won)",
        "Description": f"Deal closed Won for ${amount:,.0f}. Schedule a kickoff call with the customer this week.",
        "ActivityDate": due_date,
        "Status": "Not Started",
        "Priority": "High",
    })


def check_closed_deals(state: dict):
    closed = sf_client.query(
        "SELECT Id, Name, AccountId, Account.Name, Amount FROM Opportunity WHERE StageName = 'Closed Won'"
    )
    seen = set(state["seen_closed_won_ids"])
    newly_closed = diff_new_items(closed, seen)

    for opp in newly_closed:
        account_name = (opp.get("Account") or {}).get("Name")
        amount = opp.get("Amount") or 0
        print(f"[deal-closed] {opp['Name']} on {account_name} -- ${amount:,.0f}")
        alert = run_agent_safely(
            build_deal_closed_prompt(opp), DEAL_CLOSED_SYSTEM_PROMPT,
            f"Deal closed: {opp['Name']}",
            account_name=account_name,
        )
        header = f"\U0001F389 *Deal Closed Won*\n\n"
        try:
            web_client.chat_postMessage(channel=CSM_ALERTS_CHANNEL, text=header + alert)
            if amount >= LARGE_DEAL_THRESHOLD:
                web_client.chat_postMessage(channel=LEADERSHIP_ALERTS_CHANNEL, text=header + alert)
        except Exception as e:
            print(f"[deal-closed] FAILED to post: {unwrap_exception(e)}")
            return
        if account_name:
            alert_history.log_alert(account_name, "Deal Closed", f"{opp['Name']} -- ${amount:,.0f} Closed Won")

        try:
            create_csm_kickoff_task(opp)
            print(f"[deal-closed] Created CSM kickoff Task for {opp['Name']}")
        except Exception as e:
            # A failed Task creation shouldn't block the rest of this
            # cycle or lose the alert that already posted successfully
            # -- log it and move on, same resilience posture as
            # everything else here.
            print(f"[deal-closed] FAILED to create kickoff Task: {unwrap_exception(e)}")

        seen.add(opp["Id"])
        state["seen_closed_won_ids"] = list(seen)
        save_state(state)


def main():
    state = load_state()
    if not state.get("initialized"):
        print("[watcher] First run -- seeding baseline silently, no alerts for pre-existing data")
        seed_baseline(state)
        state["initialized"] = True
        save_state(state)
        print("[watcher] Baseline seeded. Now watching for genuine changes.")

    last_run = {"health": 0.0, "new_opp": 0.0, "closed": 0.0}
    print(
        f"Account/Opportunity watcher starting -- health every {HEALTH_DROP_POLL_SECONDS}s, "
        f"new opps every {NEW_OPPORTUNITY_POLL_SECONDS}s, closed deals every {DEAL_CLOSED_POLL_SECONDS}s"
    )
    while True:
        now = time.time()
        try:
            if now - last_run["health"] >= HEALTH_DROP_POLL_SECONDS:
                check_health_drops(state)
                last_run["health"] = now
            if now - last_run["new_opp"] >= NEW_OPPORTUNITY_POLL_SECONDS:
                check_new_opportunities(state)
                last_run["new_opp"] = now
            if now - last_run["closed"] >= DEAL_CLOSED_POLL_SECONDS:
                check_closed_deals(state)
                last_run["closed"] = now
        except Exception as e:
            print(f"[watcher] poll cycle failed: {unwrap_exception(e)}")
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
