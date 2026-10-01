#!/usr/bin/env python3
"""
Linear GraphQL API client — minimal version for HDD demos.
Uses only stdlib (no pip dependencies).

Usage:
  python scripts/linear_client.py get DEMO-1
  python scripts/linear_client.py get DEMO-1 --json
  python scripts/linear_client.py create "Title" ["Description"] [--parent DEMO-1]
  python scripts/linear_client.py update DEMO-1 "New description"
  python scripts/linear_client.py move DEMO-1 "In Progress"
  python scripts/linear_client.py comment DEMO-1 "Evidence message"
  python scripts/linear_client.py list [--state "In Progress"]
"""
import os
import sys
import json
import urllib.request

API_URL = os.environ.get("LINEAR_API_URL", "https://api.linear.app/graphql")


def _load_env():
    """Load all variables from .env file (project .env takes priority)."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
    return env_vars


def _get_api_key():
    """Load API key from .env file first, then environment."""
    # Project .env takes priority over shell environment
    env_vars = _load_env()
    key = env_vars.get("LINEAR_API_KEY")
    if key:
        # Also load team key if present
        team_key = env_vars.get("LINEAR_TEAM_KEY")
        if team_key and "LINEAR_TEAM_KEY" not in os.environ:
            os.environ["LINEAR_TEAM_KEY"] = team_key
        return key
    return os.environ.get("LINEAR_API_KEY")


def _query(query, variables=None):
    """Execute a GraphQL query against the Linear API."""
    api_key = _get_api_key()
    if not api_key:
        print("ERROR: LINEAR_API_KEY not set. Add it to .env or export it.", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            if "errors" in data:
                print("GraphQL errors:", json.dumps(data["errors"], indent=2), file=sys.stderr)
                sys.exit(1)
            return data
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)


def get_issue(issue_id):
    """Get issue by identifier (e.g., 'HAR-1'). Returns dict or None."""
    result = _query("""
        query($id: String!) {
            issueVcsByIdentifier: issue(id: $id) {
                id identifier title description
                state { name }
                priority
                createdAt startedAt completedAt
                parent { identifier }
                labels { nodes { name } }
            }
        }
    """, {"id": issue_id})
    # Try direct lookup first
    issue = result.get("data", {}).get("issueVcsByIdentifier")
    if issue:
        return issue

    # Fallback: search by filtering team issues
    parts = issue_id.split("-")
    if len(parts) == 2:
        team_key = parts[0]
        result = _query("""
            query($teamKey: String!) {
                teams(filter: { key: { eq: $teamKey } }) {
                    nodes {
                        issues(first: 100) {
                            nodes {
                                id identifier title description
                                state { name }
                                priority
                                createdAt startedAt completedAt
                                parent { identifier }
                                labels { nodes { name } }
                            }
                        }
                    }
                }
            }
        """, {"teamKey": team_key})
        teams = result.get("data", {}).get("teams", {}).get("nodes", [])
        if teams:
            for iss in teams[0].get("issues", {}).get("nodes", []):
                if iss["identifier"] == issue_id:
                    return iss
    return None


def update_issue_state(issue_id, state_name):
    """Move issue to a new state (e.g., 'In Progress', 'Done'). Returns True/False."""
    issue = get_issue(issue_id)
    if not issue:
        print(f"Issue {issue_id} not found.", file=sys.stderr)
        return False

    result = _query("""
        query($issueId: String!) {
            issue(id: $issueId) {
                team { states { nodes { id name } } }
            }
        }
    """, {"issueId": issue["id"]})

    states = result["data"]["issue"]["team"]["states"]["nodes"]
    target = next((s for s in states if s["name"] == state_name), None)
    if not target:
        available = [s["name"] for s in states]
        print(f"State '{state_name}' not found. Available: {available}", file=sys.stderr)
        return False

    _query("""
        mutation($id: String!, $stateId: String!) {
            issueUpdate(id: $id, input: { stateId: $stateId }) {
                success
            }
        }
    """, {"id": issue["id"], "stateId": target["id"]})
    return True


def add_comment(issue_id, body):
    """Add a comment to an issue (for evidence logging). Returns True/False."""
    issue = get_issue(issue_id)
    if not issue:
        print(f"Issue {issue_id} not found.", file=sys.stderr)
        return False

    _query("""
        mutation($issueId: String!, $body: String!) {
            commentCreate(input: { issueId: $issueId, body: $body }) {
                success
            }
        }
    """, {"issueId": issue["id"], "body": body})
    return True


def update_issue(issue_id, description=None, title=None):
    """Update an issue's description and/or title. Returns True/False."""
    issue = get_issue(issue_id)
    if not issue:
        print(f"Issue {issue_id} not found.", file=sys.stderr)
        return False

    input_fields = []
    variables = {"id": issue["id"]}

    if description is not None:
        variables["description"] = description
        input_fields.append("description: $description")
    if title is not None:
        variables["title"] = title
        input_fields.append("title: $title")

    if not input_fields:
        return True

    var_defs = "$id: String!"
    if description is not None:
        var_defs += ", $description: String"
    if title is not None:
        var_defs += ", $title: String"

    _query(f"""
        mutation({var_defs}) {{
            issueUpdate(id: $id, input: {{ {", ".join(input_fields)} }}) {{
                success
            }}
        }}
    """, variables)
    return True


def list_issues(team_key=None, state=None):
    """List issues, optionally filtered by state."""
    if team_key is None:
        team_key = os.environ.get("LINEAR_TEAM_KEY", "DEMO")

    result = _query("""
        query($teamKey: String!) {
            teams(filter: { key: { eq: $teamKey } }) {
                nodes {
                    issues(first: 50, orderBy: createdAt) {
                        nodes { identifier title state { name } priority }
                    }
                }
            }
        }
    """, {"teamKey": team_key})
    teams = result.get("data", {}).get("teams", {}).get("nodes", [])
    if not teams:
        return []
    issues = teams[0].get("issues", {}).get("nodes", [])
    if state:
        issues = [i for i in issues if i.get("state", {}).get("name") == state]
    return issues


def _get_team_id(team_key="DEMO"):
    """Get team ID from team key."""
    result = _query("""
        query($key: String!) {
            teams(filter: { key: { eq: $key } }) {
                nodes { id name key }
            }
        }
    """, {"key": team_key})
    teams = result.get("data", {}).get("teams", {}).get("nodes", [])
    if not teams:
        print(f"Team '{team_key}' not found.", file=sys.stderr)
        return None
    return teams[0]["id"]


def create_issue(title, description=None, team_key="DEMO", parent_id=None):
    """Create a new issue in Linear (optionally a sub-issue). Returns the issue dict or None."""
    team_id = _get_team_id(team_key)
    if not team_id:
        return None

    parent_uuid = None
    if parent_id:
        parent = get_issue(parent_id)
        if not parent:
            print(f"Parent issue {parent_id} not found.", file=sys.stderr)
            return None
        parent_uuid = parent["id"]

    variables = {
        "title": title,
        "teamId": team_id,
    }

    # Build input fields
    input_fields = "title: $title, teamId: $teamId"
    var_defs = "$title: String!, $teamId: String!"

    if description:
        variables["description"] = description
        input_fields += ", description: $description"
        var_defs += ", $description: String"

    if parent_uuid:
        variables["parentId"] = parent_uuid
        input_fields += ", parentId: $parentId"
        var_defs += ", $parentId: String"

    result = _query(f"""
        mutation({var_defs}) {{
            issueCreate(input: {{ {input_fields} }}) {{
                success
                issue {{ id identifier title url }}
            }}
        }}
    """, variables)

    issue_data = result.get("data", {}).get("issueCreate", {})
    if issue_data.get("success"):
        return issue_data.get("issue")
    return None


# ── CLI ──

def _print_issue(issue, full=False):
    """Pretty-print an issue."""
    state = issue.get("state", {}).get("name", "?")
    print(f"  {issue['identifier']}  [{state}]  {issue['title']}")
    if issue.get("description"):
        if full:
            for line in issue["description"].strip().split("\n"):
                print(f"    {line}")
        else:
            lines = issue["description"].strip().split("\n")[:2]
            for line in lines:
                print(f"    {line}")


def main():
    # Load .env before any command
    _get_api_key()

    if len(sys.argv) < 2:
        print(__doc__.strip())
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "get":
        if len(sys.argv) < 3:
            print("Usage: linear_client.py get <ISSUE_ID> [--full]")
            sys.exit(1)
        full = "--full" in sys.argv
        issue = get_issue(sys.argv[2])
        if issue and "--json" in sys.argv:
            print(json.dumps(issue))
        elif issue:
            _print_issue(issue, full=full)
        else:
            print(f"Issue {sys.argv[2]} not found.")
            sys.exit(1)

    elif cmd == "update":
        if len(sys.argv) < 4:
            print('Usage: linear_client.py update <ISSUE_ID> "<DESCRIPTION>"')
            sys.exit(1)
        ok = update_issue(sys.argv[2], description=sys.argv[3])
        if ok:
            print(f"Updated {sys.argv[2]}")
        else:
            sys.exit(1)

    elif cmd == "create":
        if len(sys.argv) < 3:
            print('Usage: linear_client.py create "<TITLE>" ["<DESCRIPTION>"]')
            sys.exit(1)
        parent_id = None
        if "--parent" in sys.argv:
            idx = sys.argv.index("--parent")
            parent_id = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
            del sys.argv[idx:idx + 2]
        title = sys.argv[2]
        description = sys.argv[3] if len(sys.argv) > 3 else None
        team_key = os.environ.get("LINEAR_TEAM_KEY", "DEMO")
        issue = create_issue(title, description, team_key, parent_id)
        if issue:
            print(f"Created: {issue['identifier']}  {issue['title']}")
            print(f"  URL: {issue.get('url', 'N/A')}")
        else:
            print("Failed to create issue.")
            sys.exit(1)

    elif cmd == "move":
        if len(sys.argv) < 4:
            print('Usage: linear_client.py move <ISSUE_ID> "<STATE>"')
            sys.exit(1)
        ok = update_issue_state(sys.argv[2], sys.argv[3])
        if ok:
            print(f"Moved {sys.argv[2]} -> {sys.argv[3]}")
        else:
            sys.exit(1)

    elif cmd == "comment":
        if len(sys.argv) < 4:
            print('Usage: linear_client.py comment <ISSUE_ID> "<BODY>"')
            sys.exit(1)
        ok = add_comment(sys.argv[2], sys.argv[3])
        if ok:
            print(f"Comment added to {sys.argv[2]}")
        else:
            sys.exit(1)

    elif cmd == "list":
        state = None
        if "--state" in sys.argv:
            idx = sys.argv.index("--state")
            if idx + 1 < len(sys.argv):
                state = sys.argv[idx + 1]
        issues = list_issues(state=state)
        if issues:
            for iss in issues:
                _print_issue(iss)
        else:
            print("No issues found.")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__.strip())
        sys.exit(1)


if __name__ == "__main__":
    main()
