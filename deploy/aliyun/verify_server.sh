#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8000}"
AUTH_USER="${BASIC_AUTH_USER:-}"
AUTH_PASS="${BASIC_AUTH_PASS:-}"
EXPECT_BASIC_AUTH="${VERIFY_EXPECT_BASIC_AUTH:-auto}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CURL_AUTH=()

if [ -x ./.venv/bin/python ]; then
  PYTHON_BIN="./.venv/bin/python"
fi

if [ -n "$AUTH_USER" ] && [ -n "$AUTH_PASS" ]; then
  CURL_AUTH=(-u "$AUTH_USER:$AUTH_PASS")
fi

if [ "$EXPECT_BASIC_AUTH" = "auto" ]; then
  case "$BASE_URL" in
    http://127.0.0.1:*|http://localhost:*|https://127.0.0.1:*|https://localhost:*)
      EXPECT_BASIC_AUTH="0"
      ;;
    *)
      EXPECT_BASIC_AUTH="1"
      ;;
  esac
fi

if [ "$EXPECT_BASIC_AUTH" = "1" ]; then
  echo "[festival-poster] Checking public URL requires shared password"
  status="$(curl -sS -o /dev/null -w "%{http_code}" "$BASE_URL/")"
  if [ "$status" != "401" ]; then
    echo "Expected unauthenticated $BASE_URL/ to return 401, got $status"
    exit 1
  fi
fi

echo "[festival-poster] Running full reproducible demo check"
DEMO_CHECK_BASE_URL="$BASE_URL" \
DEMO_CHECK_AUTH_USER="$AUTH_USER" \
DEMO_CHECK_AUTH_PASS="$AUTH_PASS" \
"$PYTHON_BIN" scripts/demo_check.py

echo "[festival-poster] OK"
