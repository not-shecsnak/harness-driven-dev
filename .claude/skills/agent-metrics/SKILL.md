---
name: agent-metrics
description: Show per-agent performance — tickets closed, average cycle time, % of gates passed on the first attempt — aggregated from the vault metrics log
user-invocable: true
allowed-tools: Bash(python3 scripts/*) Bash(python scripts/*)
---

# Agent Metrics

Print the per-agent performance table. Data comes from
`.claude/vault/_metrics/agent-log.jsonl`, appended by `scripts/close_issue.sh`
(via `scripts/agent_performance_log.py`) every time an issue is closed.

## Steps

1. **Run the aggregation**:
   ```bash
   python3 scripts/agent_performance_log.py summary
   ```
   (Use `python` if `python3` is the Windows Store stub.)

2. **Show the table as printed**: one row per agent with tickets closed,
   average cycle time (hours) and % of gates passed on the first attempt.

3. **If there is no log yet**, say so: metrics appear after the first
   `/close-issue` or `/close-feature` run.

## Notes

- Read-only: this skill never writes to the log, Linear or git.
- Environment failures (exit code 2 in `close_issue.sh`) are not counted as
  failed attempts.
- For the issues/branch/CI dashboard use `/status`; this skill only reports agents.
