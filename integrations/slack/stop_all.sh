#!/bin/bash
# Cleanly stops all 4 standing processes started by start_all.sh.
# Run from the project root:
#   ./integrations/slack/stop_all.sh

cd "$(dirname "$0")/../.."

stop_one() {
    local name=$1
    local pid_file="integrations/slack/.pids/${name}.pid"

    if [ ! -f "$pid_file" ]; then
        echo "  SKIP  $name was never started (no PID file)"
        return
    fi

    local pid
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        echo "  OK    $name stopped (was PID $pid)"
    else
        echo "  SKIP  $name already stopped"
    fi
    rm -f "$pid_file"
}

echo "Stopping all 4 processes..."
stop_one "ask-sf"
stop_one "case-alerts"
stop_one "account-opportunity"
stop_one "digest-scheduler"
