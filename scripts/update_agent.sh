#!/usr/bin/env bash
# Usage: ./scripts/update_agent.sh <id> <state> [task] [blocker]
set -euo pipefail
ID=${1:?id}; STATE=${2:?state}; TASK=${3:-}; BLOCKER=${4:-}
BODY=$(python3 - <<PY
import json
print(json.dumps({
  "id": "$ID",
  "state": "$STATE",
  **({"task": """$TASK"""} if """$TASK""" else {}),
  **({"blocker": """$BLOCKER"""} if """$BLOCKER""" else {}),
}))
PY
)
curl -sS -X POST "http://127.0.0.1:8765/api/agents/update" \
  -H 'Content-Type: application/json' -d "$BODY"
echo
