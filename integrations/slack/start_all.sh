#!/bin/bash
# Starts all 4 standing processes as background jobs, each logging to
# its own file, with PIDs tracked so status_all.sh and stop_all.sh
# can find them reliably. Run from the project root:
#   ./integrations/slack/start_all.sh
#
# Each process stays a genuinely separate OS process -- this script
# is purely a convenience for starting/stopping all 4 at once. A bug
# in one process still can't affect the others, same as running them
# by hand in separate terminals.
#
# Does NOT run setup_salesforce_auth.py -- that's an interactive
# browser login, and needs to happen once, deliberately, before this
# script runs, not as part of an unattended startup.

set -e
cd "$(dirname "$0")/../.."  # always run from the project root, regardless of where this was invoked from

mkdir -p logs
mkdir -p integrations/slack/.pids

start_one() {
    local name=$1
    local script=$2
    local log_file="logs/${name}.log"
    local pid_file="integrations/slack/.pids/${name}.pid"

    if [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        echo "  SKIP  $name already running (PID $(cat "$pid_file"))"
        return
    fi

    python3 -u "$script" > "$log_file" 2>&1 &
    echo $! > "$pid_file"
    echo "  OK    $name started (PID $!), logging to $log_file"
}

echo "Starting all 4 processes..."
# Staggered with a short pause between each -- all 4 cold-starting at
# once means all 4 hit Salesforce auth within the same instant,
# which can exhaust even the retry logic in salesforce_auth.py (found
# live: a simultaneous 4-way start caused repeated collisions across
# multiple retry rounds, not just one clean handoff). A few seconds
# between each start avoids the pile-up in the first place.
start_one "ask-sf" "integrations/slack/slack_handler.py"
sleep 3
start_one "case-alerts" "integrations/slack/case_alert_watcher.py"
sleep 3
start_one "account-opportunity" "integrations/slack/account_opportunity_watcher.py"
sleep 3
start_one "digest-scheduler" "integrations/slack/digest_scheduler.py"

echo ""
echo "Done. Use status_all.sh to check, stop_all.sh to stop, or:"
echo "  tail -f logs/<name>.log"
echo "to watch one live (case-alerts, account-opportunity, digest-scheduler, ask-sf)."
