#!/usr/bin/env python3
"""
Shared memory across invocations -- every alert that fires (case
alerts, health drops, new opportunities, deals closing) logs a record
here. Before generating a NEW alert, that account's history is looked
up and handed to Claude as context, so it can recognize "this is the
second time this month" rather than treating every alert as an
isolated, first-time event.

APPEND-ONLY JSONL, DELIBERATELY, NOT A SINGLE JSON LIST: multiple
processes (case_alert_watcher.py, account_opportunity_watcher.py) log
to this same file concurrently. A read-modify-write pattern here
would be exactly the class of race condition already found and fixed
today in salesforce_auth.py -- two processes reading the same list,
each appending their own entry, one write clobbering the other's.
Appending one complete JSON line at a time avoids that; a read
tolerates missing the very latest in-flight write (it'll be there
next time), which is a much lower-consequence race than silently
losing a whole record.
"""

import json
import time
from pathlib import Path

HISTORY_FILE = Path(__file__).parent / ".alert_history.jsonl"


def log_alert(account: str, trigger_type: str, summary: str):
    """Appends one record."""
    record = {
        "account": account,
        "trigger_type": trigger_type,
        "summary": summary,
        "timestamp": time.strftime("%Y-%m-%d %H:%M"),
    }
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")


def get_alert_history(account: str) -> list:
    """Returns every past record for this account, oldest first.
    Skips a corrupted/partial line rather than crashing -- cheap
    defensiveness given this file is written by multiple concurrent
    processes."""
    if not HISTORY_FILE.exists():
        return []
    records = []
    with open(HISTORY_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("account") == account:
                records.append(record)
    return records


def format_history_for_prompt(account: str) -> str:
    """Pure, testable. Turns history into a short block to append to
    a prompt -- empty string if there's no history, so callers can
    always append this without needing a conditional themselves."""
    history = get_alert_history(account)
    if not history:
        return ""
    lines = [f"- {r['timestamp']} ({r['trigger_type']}): {r['summary']}" for r in history]
    return (
        f"\n\nPrior alert history for this account ({len(history)} previous alert(s)):\n"
        + "\n".join(lines)
        + "\n\nConsider whether this is part of a recurring pattern, not an isolated event."
    )
