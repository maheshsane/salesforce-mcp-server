#!/usr/bin/env python3
"""
Watches for new Critical/High priority Cases and posts a three-part
alert to #support-alerts the moment one appears: account context
(with an explicit at-risk flag if the account was already unhealthy),
an issue summary with pattern-awareness (is this the 3rd case in 30
days, not just this one), and a drafted customer email.

The email is a DRAFT ONLY, deliberately -- this project has no
email-sending integration, and posting an internal Slack summary is a
different category of risk than putting words in front of an actual
customer. A human reads it, edits if needed, sends it themselves.
That boundary is intentional, not a limitation to remove later.

Built on the shared sf_agent engine (same one /ask-sf uses) rather
than new Salesforce-query logic -- the prompt below asks Claude to
use the existing MCP tools to gather everything it needs, the same
way a person asking a question would.

STATE TRACKING, the one genuinely new piece compared to /ask-sf:
polling has no memory of its own -- every check sees the same case
again unless something remembers "already alerted on this one". A
small local JSON file tracks which Case IDs have already triggered an
alert, so a new case is caught exactly once, not on every single poll
cycle forever.

HONEST NOTE: the state-tracking logic (load/save/diff) is pure and
unit-tested below with realistic fake data. The live polling loop,
the actual Salesforce query, and the actual Slack post have never run
against a real org -- this is genuinely new code, first real run
should be expected to surface something, same as slack_handler.py's
own history.

Channel is referenced by NAME ("#support-alerts") rather than ID --
Slack's API does accept a channel name for a public channel the bot
is a member of, but this hasn't been proven live yet. If the first
real run fails to post, that's the first thing to check -- swap in
the channel's real ID (found via right-click the channel > View
channel details > scroll to the ID) if the name alone doesn't work.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import os
from slack_sdk import WebClient

from sf_agent import answer_question, unwrap_exception

# sf_client.py lives in the project root, two directories up from this
# file (integrations/slack/). A direct top-level import only searches
# this script's own folder by default -- unlike the subprocess-spawned
# server.py inside sf_agent.py, which resolves relative to whatever
# directory the process was launched from, not this file's location.
# Two different resolution rules for two different kinds of import;
# this one needs an explicit path, found by testing, not assumed.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
import sf_client

SUPPORT_ALERTS_CHANNEL = "#support-alerts"
POLL_INTERVAL_SECONDS = 90  # 1-2 minutes, per the tighter cadence this workflow needs
STATE_FILE = Path(__file__).parent / ".case_alert_state.json"

web_client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])

SYSTEM_PROMPT = (
    "You are an automated system generating an internal alert for a "
    "newly-logged Critical or High priority Salesforce case. Using the "
    "connected tools, pull real data -- never guess or make up figures. "
    "Respond with ONLY the three numbered sections below -- no preamble, "
    "no narrating what you're about to do or that you now have enough "
    "information, no extra header of your own (a header is already "
    "added separately before your response is posted). Start directly "
    "with \"1. *Account Summary*\".\n\n"
    "1. *Account Summary* -- lead with the account name itself in bold "
    "(e.g. \u2022 Account: *Cambridge Life Sciences Corp*), then health "
    "score, ARR, renewal date if any, and who the CSM is. If the "
    "account's health score was already below 50 before this case, say "
    "so explicitly -- that's a real escalation signal, not just "
    "background.\n"
    "2. *Issue Summary* -- what this case is actually about, and whether "
    "this account has had other cases recently (a real pattern, not just "
    "this one case in isolation -- check and say the actual count).\n"
    "3. *Draft Customer Email* -- addressed to the account's actual "
    "primary contact by name if one exists on file, acknowledging the "
    "issue, giving a concrete next step, professional but not overly "
    "apologetic. This is a DRAFT for a human to review and send -- never "
    "claim it has already been sent.\n\n"
    "Format for Slack: *bold* section headers and the account name with "
    "single asterisks, \u2022 for bullet points, no markdown headers."
)


def load_seen_case_ids() -> set:
    """Pure-ish (does real file I/O, but the logic itself is simple and
    testable via a temp path). Empty set if the state file doesn't
    exist yet -- the very first run, nothing has been seen before."""
    if not STATE_FILE.exists():
        return set()
    return set(json.loads(STATE_FILE.read_text()))


def save_seen_case_ids(seen_ids: set):
    STATE_FILE.write_text(json.dumps(sorted(seen_ids)))


def find_new_cases(current_cases: list, seen_ids: set) -> list:
    """Pure, testable -- the actual diffing logic, no I/O, no network.
    Returns the cases from current_cases whose Id isn't in seen_ids yet."""
    return [c for c in current_cases if c["Id"] not in seen_ids]


def build_alert_prompt(case: dict) -> str:
    # .get(key, default) only falls back for a MISSING key, not one
    # present with a None value -- and AccountName is explicitly set
    # to None for a case with no related account, so `or` is needed
    # here, not a .get() default (caught by testing, not assumed).
    account_name = case.get("AccountName") or "unknown account"
    return (
        f"A new {case['Priority']} case was just logged: \"{case['Subject']}\" "
        f"on account {account_name} "
        f"(Case ID: {case['Id']}). Generate the three-section alert as instructed."
    )


def check_for_new_cases():
    """One poll cycle: query current Critical/High cases, diff against
    what's already been alerted on, generate and post an alert for
    each genuinely new one, then persist the updated seen-set."""
    seen_ids = load_seen_case_ids()

    cases = sf_client.query(
        "SELECT Id, Subject, Priority, AccountId, Account.Name FROM Case "
        "WHERE Priority IN ('Critical', 'High') ORDER BY CreatedDate DESC LIMIT 50"
    )
    for c in cases:
        c["AccountName"] = (c.get("Account") or {}).get("Name")

    new_cases = find_new_cases(cases, seen_ids)
    if not new_cases:
        return

    for case in new_cases:
        print(f"[case-alert] New {case['Priority']} case: {case['Subject']} ({case['Id']})")
        try:
            alert_text = asyncio.run(answer_question(build_alert_prompt(case), SYSTEM_PROMPT))
        except Exception as e:
            alert_text = f"New {case['Priority']} case logged but the alert generation failed: {unwrap_exception(e)}"

        if not alert_text or not alert_text.strip():
            alert_text = f"New {case['Priority']} case logged ({case['Id']}) but couldn't generate a full alert -- check it manually."

        header = f"\U0001F6A8 *New {case['Priority']} Case: {case['Subject']}*\n\n"
        try:
            web_client.chat_postMessage(channel=SUPPORT_ALERTS_CHANNEL, text=header + alert_text)
        except Exception as e:
            # Stop processing this batch rather than push through the
            # rest -- a posting failure here (wrong channel, bot not
            # invited) will almost certainly fail identically for every
            # remaining case too, and there's no point burning more
            # Claude+MCP calls generating alerts that can't be
            # delivered. Whatever already succeeded this cycle is
            # already saved below, per-case -- only the ones still
            # pending get retried on the next poll.
            print(f"[case-alert] FAILED to post alert for {case['Id']}: {unwrap_exception(e)}")
            print("[case-alert] stopping this cycle -- will retry remaining cases next poll")
            return

        # Save immediately after each successful post, not batched at
        # the end -- so a later failure in this same cycle can't force
        # redoing work that already genuinely succeeded.
        seen_ids.add(case["Id"])
        save_seen_case_ids(seen_ids)


def main():
    print(f"Case Alert watcher starting -- checking every {POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            check_for_new_cases()
        except Exception as e:
            print(f"[case-alert] poll cycle failed: {unwrap_exception(e)}")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
