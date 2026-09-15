# -*- coding: utf-8 -*-
"""Research coordinator for a v2 trend core and confirmed reverse-T satellite."""
from __future__ import print_function

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Stragety.MiniQMT_Stragety.core.satellite_reverse_t import satellite_shares


RESEARCH_ONLY = True


def build_budget(position, regime, risk_state):
    t_shares = satellite_shares(position, regime, risk_state)
    return {
        "position": int(position),
        "regime": regime,
        "risk_state": risk_state,
        "core_floor_shares": int(position) - t_shares,
        "satellite_shares": t_shares,
        "allow_forward_t": False,
        "allow_reverse_t": t_shares > 0,
        "max_cycles_per_day": 1,
        "new_entry_cutoff": "14:20:00",
        "session_exit": "15:00:00",
        "status": "RESEARCH_ONLY",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--position", type=int, required=True)
    parser.add_argument(
        "--regime", choices=("STRONG_BULL", "BULL", "SIDEWAYS", "BEAR"),
        required=True)
    parser.add_argument("--risk-state", default="NORMAL")
    parser.add_argument("--mode", choices=("signal", "live"), default="signal")
    args = parser.parse_args(argv)
    if args.mode == "live":
        raise RuntimeError("RESEARCH_ONLY: hybrid acceptance has not passed")
    print(json.dumps(
        build_budget(args.position, args.regime, args.risk_state),
        ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
