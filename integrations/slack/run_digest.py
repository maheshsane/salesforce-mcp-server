#!/usr/bin/env python3
"""
Fires one digest immediately, regardless of the clock -- for testing
digest content without waiting on digest_scheduler.py's actual
scheduled time. Doesn't touch the scheduler's state file, so running
this has no effect on whether the real scheduler fires later today.

Usage: python3 run_digest.py <csm|sales|presales|marketing|leadership>
       python3 run_digest.py all    -- runs all 5, one after another
"""

import sys

from digest_scheduler import DIGESTS, run_digest


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    target = sys.argv[1]
    valid_keys = [d["key"] for d in DIGESTS]

    if target == "all":
        for d in DIGESTS:
            run_digest(d)
        return

    if target not in valid_keys:
        print(f"Unknown digest '{target}'. Valid options: {', '.join(valid_keys)}, or 'all'")
        sys.exit(1)

    digest = next(d for d in DIGESTS if d["key"] == target)
    run_digest(digest)


if __name__ == "__main__":
    main()
