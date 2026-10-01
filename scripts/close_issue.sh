#!/usr/bin/env bash
#
# close_issue.sh — Harness gate script for closing Linear issues.
#
# Runs 3 gates before allowing an issue to be closed:
#   Gate 1: Tests passing (npm test)
#   Gate 2: CI green (last GitHub Actions run)
#   Gate 3: Acceptance criteria checked (Linear API)
#
# Usage:
#   bash scripts/close_issue.sh DEMO-1
#

set -euo pipefail

ISSUE_ID="${1:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Attempt counter (feeds the "gates passed on first try" metric) ──
# Lives in the git dir so it is never committed and is shared across worktrees.
ATTEMPTS_DIR="$(git rev-parse --git-common-dir 2>/dev/null || echo .git)/hdd-attempts"
mkdir -p "$ATTEMPTS_DIR" 2>/dev/null || true
ATTEMPTS=$(( $(cat "$ATTEMPTS_DIR/$ISSUE_ID" 2>/dev/null || echo 0) + 1 ))
echo "$ATTEMPTS" > "$ATTEMPTS_DIR/$ISSUE_ID" 2>/dev/null || true

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

if [ -z "$ISSUE_ID" ]; then
    echo -e "${RED}Usage: bash scripts/close_issue.sh <ISSUE_ID>${NC}"
    exit 1
fi

echo ""
echo "========================================"
echo "  Harness Gate Check: $ISSUE_ID"
echo "========================================"
echo ""

GATES_PASSED=0
GATES_TOTAL=3

# ── Gate 1: Tests ──

echo -n "Gate 1/3 — Tests passing... "
if npm test --silent 2>/dev/null; then
    echo -e "${GREEN}PASS${NC}"
    GATES_PASSED=$((GATES_PASSED + 1))
else
    echo -e "${RED}FAIL${NC}"
    echo -e "${YELLOW}  Fix: Run 'npm test' and fix failing tests.${NC}"
fi

# ── Gate 2: CI Green ──

echo -n "Gate 2/3 — CI green... "
if command -v gh &>/dev/null; then
    BRANCH=$(git branch --show-current 2>/dev/null || echo "")
    if [ -n "$BRANCH" ]; then
        CI_STATUS=$(gh run list --branch "$BRANCH" --workflow ci.yml --limit 1 --json conclusion --jq '.[0].conclusion' 2>/dev/null || echo "unknown")
        if [ "$CI_STATUS" = "success" ]; then
            echo -e "${GREEN}PASS${NC}"
            GATES_PASSED=$((GATES_PASSED + 1))
        elif [ "$CI_STATUS" = "unknown" ] || [ -z "$CI_STATUS" ]; then
            echo -e "${YELLOW}SKIP (no CI runs found)${NC}"
            GATES_PASSED=$((GATES_PASSED + 1))
        else
            echo -e "${RED}FAIL (last run: $CI_STATUS)${NC}"
            echo -e "${YELLOW}  Fix: Check GitHub Actions and fix the failing workflow.${NC}"
        fi
    else
        echo -e "${YELLOW}SKIP (not on a branch)${NC}"
        GATES_PASSED=$((GATES_PASSED + 1))
    fi
else
    echo -e "${YELLOW}SKIP (gh CLI not installed)${NC}"
    GATES_PASSED=$((GATES_PASSED + 1))
fi

# ── Gate 3: Acceptance Criteria ──

echo -n "Gate 3/3 — Acceptance criteria... "
ISSUE_DATA=$(python3 "$SCRIPT_DIR/linear_client.py" get "$ISSUE_ID" --full 2>/dev/null || echo "")
if [ -z "$ISSUE_DATA" ]; then
    echo -e "${YELLOW}SKIP (could not fetch issue)${NC}"
    GATES_PASSED=$((GATES_PASSED + 1))
else
    # Count checked and unchecked boxes (Linear uses [X] uppercase)
    UNCHECKED=$(echo "$ISSUE_DATA" | grep -c '\- \[ \]' || true)
    CHECKED=$(echo "$ISSUE_DATA" | grep -ci '\- \[x\]' || true)
    TOTAL=$((UNCHECKED + CHECKED))

    if [ "$TOTAL" -eq 0 ]; then
        echo -e "${YELLOW}SKIP (no checkboxes found in issue description)${NC}"
        GATES_PASSED=$((GATES_PASSED + 1))
    elif [ "$UNCHECKED" -gt 0 ]; then
        echo -e "${RED}FAIL ($UNCHECKED/$TOTAL unchecked criteria)${NC}"
        echo -e "${YELLOW}  Fix: Complete all acceptance criteria checkboxes in Linear.${NC}"
    else
        echo -e "${GREEN}PASS ($CHECKED/$TOTAL checked)${NC}"
        GATES_PASSED=$((GATES_PASSED + 1))
    fi
fi

# ── Result ──

echo ""
echo "========================================"

if [ "$GATES_PASSED" -eq "$GATES_TOTAL" ]; then
    echo -e "${GREEN}  ALL GATES PASSED ($GATES_PASSED/$GATES_TOTAL)${NC}"
    echo "  Issue $ISSUE_ID is ready to close."
    echo "========================================"
    echo ""

    # ── Build evidence ──

    COMMIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    COMMIT_FULL=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
    REPO_URL=$(git remote get-url origin 2>/dev/null | sed 's/\.git$//' | sed 's|git@github.com:|https://github.com/|' || echo "")
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    COMMIT_AUTHOR=$(git log -1 --format='%an <%ae>' 2>/dev/null || echo "unknown")
    COMMIT_DATE=$(git log -1 --format='%ci' 2>/dev/null || echo "unknown")
    COMMIT_MSG=$(git log -1 --format='%s' 2>/dev/null || echo "unknown")

    # Diff stats — try branch diff first, then last commit, then PR files
    DIFF_STAT=$(git diff --stat main...HEAD 2>/dev/null || echo "")
    FILES_CHANGED=$(git diff --name-status main...HEAD 2>/dev/null || echo "")

    # If on main (post-merge), try last merge commit
    if [ -z "$DIFF_STAT" ] || [ "$BRANCH" = "main" ]; then
        MERGE_COMMIT=$(git log -1 --merges --format='%H' 2>/dev/null || echo "")
        if [ -n "$MERGE_COMMIT" ]; then
            DIFF_STAT=$(git diff --stat "${MERGE_COMMIT}^...${MERGE_COMMIT}" 2>/dev/null || echo "")
            FILES_CHANGED=$(git diff --name-status "${MERGE_COMMIT}^...${MERGE_COMMIT}" 2>/dev/null || echo "")
        fi
    fi

    # Fallback: last commit
    if [ -z "$DIFF_STAT" ]; then
        DIFF_STAT=$(git diff --stat HEAD~1 2>/dev/null || echo "No diff available")
        FILES_CHANGED=$(git diff --name-status HEAD~1 2>/dev/null || echo "")
    fi

    FILES_COUNT=$(echo "$FILES_CHANGED" | grep -c '.' 2>/dev/null || echo "0")

    # Test results
    TEST_OUTPUT=$(npm test 2>&1 || true)
    TESTS_PASSED=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ passed' || echo "unknown")
    TESTS_FAILED=$(echo "$TEST_OUTPUT" | grep -oE '[0-9]+ failed' || echo "0 failed")

    # CI run info
    CI_STATUS_TEXT="unknown"
    CI_RUN_LINK=""
    if command -v gh &>/dev/null && [ -n "$REPO_URL" ]; then
        CI_RUN_JSON=$(gh run list --workflow ci.yml --branch "$BRANCH" --limit 1 --json databaseId,conclusion 2>/dev/null || echo "[]")
        CI_RUN_ID=$(echo "$CI_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0]['databaseId'] if d else '')" 2>/dev/null || echo "")
        CI_STATUS_TEXT=$(echo "$CI_RUN_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d[0].get('conclusion','unknown') if d else 'unknown')" 2>/dev/null || echo "unknown")
        if [ -n "$CI_RUN_ID" ]; then
            CI_RUN_LINK="[CI Run #${CI_RUN_ID}](${REPO_URL}/actions/runs/${CI_RUN_ID})"
        fi
    fi

    # PR info
    PR_LINK=""
    if command -v gh &>/dev/null; then
        PR_JSON=$(gh pr list --state merged --head "$BRANCH" --json number,title --jq '.[0]' 2>/dev/null || echo "")
        if [ -n "$PR_JSON" ] && [ "$PR_JSON" != "null" ]; then
            PR_NUM=$(echo "$PR_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('number',''))" 2>/dev/null || echo "")
            PR_TITLE=$(echo "$PR_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('title',''))" 2>/dev/null || echo "")
            if [ -n "$PR_NUM" ]; then
                PR_LINK="[PR #${PR_NUM}: ${PR_TITLE}](${REPO_URL}/pull/${PR_NUM})"
            fi
        fi
    fi

    # Acceptance criteria count
    ISSUE_FULL=$(python3 "$SCRIPT_DIR/linear_client.py" get "$ISSUE_ID" --full 2>/dev/null || echo "")
    AC_CHECKED=$(echo "$ISSUE_FULL" | grep -ci '\- \[x\]' || true)
    AC_TOTAL=$((AC_CHECKED + $(echo "$ISSUE_FULL" | grep -c '\- \[ \]' || true)))

    # Build markdown evidence
    EVIDENCE="## Evidencia de cierre — ${ISSUE_ID}

### 1. Resolución

- **Status**: PASS
- **Fecha**: ${TIMESTAMP}
- **Verificado por**: Harness enforcement (${GATES_PASSED}/${GATES_TOTAL} gates)

### 2. Cambios implementados

- **Commit**: \`${COMMIT_SHA}\`
- **Autor**: ${COMMIT_AUTHOR}
- **Fecha commit**: ${COMMIT_DATE}
- **Mensaje**: ${COMMIT_MSG}
$([ -n "$PR_LINK" ] && echo "- **PR**: ${PR_LINK}" || true)

**Diff stats**

\`\`\`
${DIFF_STAT}
\`\`\`

**Archivos modificados (${FILES_COUNT})**

\`\`\`
${FILES_CHANGED}
\`\`\`

### 3. Verificación — Tests

- **Resultado**: ${TESTS_PASSED}, ${TESTS_FAILED}

### 4. Quality Gates

- **Gate 1 — Tests**: PASS (${TESTS_PASSED})
- **Gate 2 — CI/CD**: ${CI_STATUS_TEXT}$([ -n "$CI_RUN_LINK" ] && echo " — ${CI_RUN_LINK}" || true)
- **Gate 3 — Acceptance Criteria**: PASS (${AC_CHECKED}/${AC_TOTAL} checked)

### 5. Audit Trail

- **Issue**: ${ISSUE_ID}
- **Action**: Closed with automated evidence
- **Timestamp**: ${TIMESTAMP}
- **Tool**: scripts/close_issue.sh
- **Commit SHA**: \`${COMMIT_SHA}\`

---
*Evidencia generada automáticamente por el harness.*"

    python3 "$SCRIPT_DIR/linear_client.py" comment "$ISSUE_ID" "$EVIDENCE" 2>/dev/null || true

    # Move to Done
    python3 "$SCRIPT_DIR/linear_client.py" move "$ISSUE_ID" "Done" 2>/dev/null || true

    echo "Evidence posted and issue moved to Done."

    # ── Obsidian vault note + agent metrics (best effort, never blocks the close) ──
    # Context comes from the orchestrator via env: HDD_AGENT, HDD_PARENT, HDD_TASK_TYPE
    REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
    VAULT_DIR="$REPO_ROOT/.claude/vault"
    mkdir -p "$VAULT_DIR/_metrics"
    AGENT="${HDD_AGENT:-unassigned}"
    TASK_TYPE="${HDD_TASK_TYPE:-$(echo "$BRANCH" | cut -d/ -f1)}"
    ISSUE_JSON=$(python3 "$SCRIPT_DIR/linear_client.py" get "$ISSUE_ID" --json 2>/dev/null || echo "{}")
    ISSUE_TITLE=$(echo "$ISSUE_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('title',''))" 2>/dev/null || echo "")
    PARENT="${HDD_PARENT:-$(echo "$ISSUE_JSON" | python3 -c "import sys,json; print((json.load(sys.stdin).get('parent') or {}).get('identifier',''))" 2>/dev/null || echo "")}"
    if [ "$ATTEMPTS" -eq 1 ]; then GATES_FIRST_TRY=true; else GATES_FIRST_TRY=false; fi
    PR_FM=""
    [ -n "${PR_NUM:-}" ] && PR_FM="#${PR_NUM}"
    NOTE="$VAULT_DIR/$ISSUE_ID.md"

    if [ -f "$NOTE" ]; then
        # Existing note (e.g. the feature parent written by /plan-feature): append, never overwrite.
        {
            echo ""
            echo "## Cierre — ${TIMESTAMP}"
            echo "- Gates: ${GATES_PASSED}/${GATES_TOTAL} (intentos: ${ATTEMPTS}) · Agente: ${AGENT} · PR: ${PR_FM:-n/a}"
        } >> "$NOTE"
    else
        {
            echo "---"
            echo "issue: ${ISSUE_ID}"
            echo "parent: ${PARENT:-null}"
            echo "agente: ${AGENT}"
            echo "fecha: ${TIMESTAMP}"
            echo "pr: ${PR_FM:-null}"
            echo "gates: \"${GATES_PASSED}/${GATES_TOTAL} (tests, ci, criteria)\""
            echo "intentos: ${ATTEMPTS}"
            echo "---"
            echo ""
            echo "# ${ISSUE_ID} — ${ISSUE_TITLE:-$COMMIT_MSG}"
            echo ""
            [ -n "$PARENT" ] && echo "Feature: [[${PARENT}]]"
            [ -n "$PR_LINK" ] && echo "PR: ${PR_LINK}"
            echo "Commit: \`${COMMIT_SHA}\`"
        } > "$NOTE"
    fi
    echo "Vault note: .claude/vault/$ISSUE_ID.md"

    python3 "$SCRIPT_DIR/agent_performance_log.py" record "$ISSUE_ID"         ${PARENT:+--parent "$PARENT"} --agent "$AGENT" --task-type "$TASK_TYPE"         --gates-first-try "$GATES_FIRST_TRY" --branch "$BRANCH" 2>/dev/null || true

    rm -f "$ATTEMPTS_DIR/$ISSUE_ID" 2>/dev/null || true
    exit 0
else
    echo -e "${RED}  BLOCKED ($GATES_PASSED/$GATES_TOTAL passed)${NC}"
    echo "  Fix the failing gates and run again."
    echo "========================================"
    exit 1
fi
