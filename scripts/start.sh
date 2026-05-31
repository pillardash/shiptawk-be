#!/usr/bin/env sh
set -eu

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"
FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"
PROXY_HEADERS="${PROXY_HEADERS:-true}"

set -- uvicorn app.main:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers "${WORKERS}"

if [ "${PROXY_HEADERS}" = "true" ]; then
  set -- "$@" --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS}"
fi

exec "$@"
