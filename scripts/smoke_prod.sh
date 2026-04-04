#!/usr/bin/env bash

set -euo pipefail

PROXY_URL="${PROXY_URL:-http://localhost:${PROXY_PORT:-19090}}"
API_URL="${API_URL:-http://localhost:${API_PORT:-19091}}"
ANALYZER_WAIT_SECONDS="${ANALYZER_WAIT_SECONDS:-6}"
PROVIDER_MODEL="${PROVIDER_MODEL:-gpt-4o-mini}"
REQUEST_PATH="${REQUEST_PATH:-/v1/chat/completions}"
USER_MESSAGE="${USER_MESSAGE:-smoke test}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd python3

if [[ -z "${PROVIDER_API_KEY:-}" ]]; then
  echo "PROVIDER_API_KEY is required" >&2
  exit 1
fi

SCOPED_HASH="${SCOPED_HASH:-$(python3 - <<'PY'
import hashlib
import os
print(hashlib.sha256(os.environ['PROVIDER_API_KEY'].encode()).hexdigest()[:32])
PY
)}"

echo "[1/7] Proxy health: ${PROXY_URL}/health"
curl -sf "${PROXY_URL}/health" >/dev/null

echo "[2/7] API health (scoped overview): ${API_URL}/api/overview?key_hashes=${SCOPED_HASH}"
curl -sf "${API_URL}/api/overview?key_hashes=${SCOPED_HASH}" >/dev/null

echo "[3/7] Send one request through proxy"
curl -sf -X POST "${PROXY_URL}${REQUEST_PATH}" \
  -H "authorization: Bearer ${PROVIDER_API_KEY}" \
  -H 'content-type: application/json' \
  -d "{\"model\":\"${PROVIDER_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"${USER_MESSAGE}\"}]}" >/dev/null

echo "[4/7] Wait analyzer to process (${ANALYZER_WAIT_SECONDS}s)"
sleep "${ANALYZER_WAIT_SECONDS}"

echo "[5/7] Query latest scoped conversation id"
conv_json="$(curl -sf "${API_URL}/api/conversations?key_hashes=${SCOPED_HASH}&page=1&page_size=1")"
conv_id="$(python3 - <<'PY' "${conv_json}" "${REQUEST_PATH}"
import json, sys
d = json.loads(sys.argv[1])
request_path = sys.argv[2]
assert d['items'], 'no scoped conversations found'
item = d['items'][0]
assert item.get('path') == request_path, item.get('path')
print(item['id'])
PY
)"
echo "conversation_id=${conv_id}"

echo "[6/7] Validate raw traceback includes request_body/response_body"
raw_json="$(curl -sf "${API_URL}/api/conversations/${conv_id}/raw?key_hashes=${SCOPED_HASH}")"
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

echo "[7/7] Validate optional admin access"
if [[ -n "${ADMIN_KEY_HASH:-}" ]]; then
  curl -sf "${API_URL}/api/admin/status" \
    -H "Authorization: Bearer ${ADMIN_KEY_HASH}" >/dev/null
  echo "admin access validated"
else
  echo "ADMIN_KEY_HASH not set, skipping admin validation"
fi

echo "Smoke test passed: proxy -> analyzer -> scoped api -> raw traceback -> optional admin"