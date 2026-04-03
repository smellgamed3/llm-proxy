#!/usr/bin/env bash

set -euo pipefail

PROXY_URL="${PROXY_URL:-http://localhost:${PROXY_PORT:-19090}}"
API_URL="${API_URL:-http://localhost:${API_PORT:-19091}}"
ANALYZER_WAIT_SECONDS="${ANALYZER_WAIT_SECONDS:-6}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd python3

echo "[1/6] Proxy health: ${PROXY_URL}/health"
curl -sf "${PROXY_URL}/health" >/dev/null

echo "[2/6] API health (overview): ${API_URL}/api/overview"
curl -sf "${API_URL}/api/overview" >/dev/null

echo "[3/6] Send one request through proxy"
curl -sf -X POST "${PROXY_URL}/v1/chat/completions" \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"smoke test"}]}' >/dev/null

echo "[4/6] Wait analyzer to process (${ANALYZER_WAIT_SECONDS}s)"
sleep "${ANALYZER_WAIT_SECONDS}"

echo "[5/6] Query latest conversation id"
conv_id="$(curl -sf "${API_URL}/api/conversations?page=1&page_size=1" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["items"], "no conversations found"; print(d["items"][0]["id"])')"
echo "conversation_id=${conv_id}"

echo "[6/6] Validate raw traceback includes request_body/response_body"
raw_json="$(curl -sf "${API_URL}/api/conversations/${conv_id}/raw")"
python3 - <<'PY' "${raw_json}"
import json
import sys

data = json.loads(sys.argv[1])
request_body = data.get("request_body")
response_body = data.get("response_body")
if not request_body:
    raise SystemExit("request_body missing in /raw response")
if response_body is None:
    raise SystemExit("response_body missing in /raw response")
print("raw traceback validated")
PY

echo "Smoke test passed: proxy -> analyzer -> api -> raw traceback"