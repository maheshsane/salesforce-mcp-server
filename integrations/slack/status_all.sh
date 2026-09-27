#!/bin/bash
# One-shot check: which of the 4 standing processes are actually
# running right now. Run from the project root:
#   ./integrations/slack/status_all.sh

cd "$(dirname "$0")/../.."

check_one() {
    local name=$1
    local pid_file="integrations/slack/.pids/${name}.pid"

    if [ ! -f "$pid_file" ]; then
        echo "  NOT STARTED  $name"
        return
    fi

    local pid
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        echo "  RUNNING      $name (PID $pid)"
    else
        echo "  STOPPED      $name (stale PID file, process no longer running)"
    fi
}

echo "Status of all 4 processes:"
check_one "ask-sf"
check_one "case-alerts"
check_one "account-opportunity"
check_one "digest-scheduler"
