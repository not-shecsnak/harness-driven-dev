#!/usr/bin/env python3
"""
Agent performance log — one JSONL line per closed ticket.
Uses only stdlib. Called by close_issue.sh on every successful close.

Usage:
  python scripts/agent_performance_log.py record DEMO-2 --parent DEMO-1 \
      --agent agent-developer --task-type feat --gates-first-try true
  python scripts/agent_performance_log.py summary

Log file: <repo>/.claude/vault/_metrics/agent-log.jsonl
Fields: date, ticket, parent, agent, task_type, gates_first_try,
        cycle_hours, pr_first_review_approved (null when unknown).
Re-recording the same ticket replaces its line (idempotent).
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG_PATH = os.path.join(REPO_ROOT, ".claude", "vault", "_metrics", "agent-log.jsonl")


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tribool(value):
    return {"true": True, "false": False}.get(str(value).lower())


def _cycle_hours(issue):
    """Hours from first start (fallback: creation) until now."""
    start = _parse_ts((issue or {}).get("startedAt")) or _parse_ts((issue or {}).get("createdAt"))
    if not start:
        return None
    return round((datetime.now(timezone.utc) - start).total_seconds() / 3600, 2)


def _pr_first_review_approved(branch):
    """True if the first submitted review of the merged/open PR approved it.
    None when there is no PR, no review (solo project) or gh is unavailable."""
    if not branch:
        return None
    try:
        out = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--head", branch,
             "--json", "reviews", "--jq", ".[0].reviews"],
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0 or not out.stdout.strip() or out.stdout.strip() == "null":
            return None
        reviews = sorted(json.loads(out.stdout), key=lambda r: r.get("submittedAt", ""))
        reviews = [r for r in reviews if r.get("state") in ("APPROVED", "CHANGES_REQUESTED")]
        return reviews[0]["state"] == "APPROVED" if reviews else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _read_log():
    if not os.path.exists(LOG_PATH):
        return []
    rows = []
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    return rows


def record(args):
    issue = None
    try:
        from linear_client import get_issue
        issue = get_issue(args.ticket)
    except (SystemExit, Exception):  # Linear unreachable -> degrade to nulls
        issue = None

    parent = args.parent or ((issue or {}).get("parent") or {}).get("identifier")
    pr_ok = _tribool(args.pr_approved) if args.pr_approved != "auto" else _pr_first_review_approved(args.branch)
    entry = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticket": args.ticket,
        "parent": parent,
        "agent": args.agent,
        "task_type": args.task_type,
        "gates_first_try": _tribool(args.gates_first_try),
        "cycle_hours": args.cycle_hours if args.cycle_hours is not None else _cycle_hours(issue),
        "pr_first_review_approved": pr_ok,
    }
    rows = [r for r in _read_log() if r.get("ticket") != args.ticket]
    rows.append(entry)
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Metrics recorded: {entry['ticket']} ({entry['agent']})")


def summary(_args):
    rows = _read_log()
    if not rows:
        print("No agent metrics yet (.claude/vault/_metrics/agent-log.jsonl).")
        return
    by_agent = {}
    for r in rows:
        by_agent.setdefault(r.get("agent") or "unassigned", []).append(r)
    header = f"{'Agent':<22}{'Closed':>7}{'Avg cycle (h)':>15}{'Gates 1st try':>15}"
    print(header)
    print("-" * len(header))
    for agent, items in sorted(by_agent.items()):
        cycles = [i["cycle_hours"] for i in items if isinstance(i.get("cycle_hours"), (int, float))]
        gated = [i["gates_first_try"] for i in items if isinstance(i.get("gates_first_try"), bool)]
        avg = f"{sum(cycles) / len(cycles):.1f}" if cycles else "n/a"
        pct = f"{100 * sum(gated) / len(gated):.0f}%" if gated else "n/a"
        print(f"{agent:<22}{len(items):>7}{avg:>15}{pct:>15}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("ticket")
    rec.add_argument("--parent")
    rec.add_argument("--agent", default="unassigned")
    rec.add_argument("--task-type", default="unspecified")
    rec.add_argument("--gates-first-try", default="unknown", choices=["true", "false", "unknown"])
    rec.add_argument("--pr-approved", default="auto", choices=["auto", "true", "false", "unknown"])
    rec.add_argument("--branch", default="")
    rec.add_argument("--cycle-hours", type=float)
    rec.set_defaults(func=record)
    sub.add_parser("summary").set_defaults(func=summary)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
