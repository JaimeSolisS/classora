#!/usr/bin/env bash
# Smoke test of the edge image (#610, ADR-0010): runs it in front of a stub API
# (edge/smoke-upstream.py) on a throwaway Docker network and checks every
# behaviour of the Caddyfile over HTTP, plus HSTS over HTTPS (Caddy's internal
# CA for localhost). Run through `make smoke-edge`; the CI `images` job runs it
# on the arm64 image.
#
#   infrastructure/docker/edge/smoke.sh <edge image>
#
# Needs Docker, curl, jq and Node 24 (to read PRODUCTION_HEADERS from
# apps/web/src/security-headers.ts). STUB_IMAGE overrides the stub's image
# (default python:3.14-slim, the API's base). Every container and the network
# are removed on exit. Prints one line per check; exits 1 if any failed.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <edge image>" >&2
  exit 2
fi
EDGE_IMAGE=$1
STUB_IMAGE=${STUB_IMAGE:-python:3.14-slim}
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
NAME=classora-edge-smoke-$$
TMP=$(mktemp -d)

cleanup() {
  docker rm -f "$NAME-api" "$NAME-http" "$NAME-https" >/dev/null 2>&1 || true
  docker network rm "$NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

failures=0
pass() { echo "ok    $1"; }
fail() {
  echo "FAIL  $1" >&2
  failures=$((failures + 1))
}
# check <description> <actual> <expected>
check() {
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1: got '$2', expected '$3'"; fi
}
# header <name> <headers file>: the first value of a response header, or empty.
header() {
  { grep -i "^$1:" "$2" || true; } | head -n 1 | sed -e 's/^[^:]*:[[:space:]]*//' | tr -d '\r'
}
# get <name> <url> [curl options...]: saves <name>.headers and <name>.body, prints the status.
get() {
  local name=$1 url=$2
  shift 2
  curl -sS -o "$TMP/$name.body" -D "$TMP/$name.headers" -w '%{http_code}' "$@" "$url"
}

expected_headers=$(node "$ROOT/apps/web/scripts/print-production-headers.js")
[ -n "$expected_headers" ] || { echo "smoke-edge: no PRODUCTION_HEADERS printed" >&2; exit 1; }

docker network create "$NAME" >/dev/null

# The stub API, reachable as api:8000 (the Caddyfile's default upstream). The
# script is copied in, so no bind mount is needed.
docker create --name "$NAME-api" --network "$NAME" --network-alias api \
  "$STUB_IMAGE" python /smoke-upstream.py >/dev/null
docker cp "$HERE/smoke-upstream.py" "$NAME-api:/smoke-upstream.py"
docker start "$NAME-api" >/dev/null

docker run -d --name "$NAME-http" --network "$NAME" -e SITE_ADDRESS=http://:8080 \
  -p 127.0.0.1::8080 "$EDGE_IMAGE" >/dev/null
docker run -d --name "$NAME-https" --network "$NAME" -e SITE_ADDRESS=https://localhost:8443 \
  -p 127.0.0.1::8443 "$EDGE_IMAGE" >/dev/null
HTTP="http://$(docker port "$NAME-http" 8080/tcp | head -n 1)"
HTTPS_PORT=$(docker port "$NAME-https" 8443/tcp | head -n 1 | sed 's/.*://')

# Wait until the edge and the stub behind it both answer (at most ~30 s).
ready=false
for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "$HTTP/api/ready" 2>/dev/null; then
    ready=true
    break
  fi
  sleep 0.5
done
if [ "$ready" != true ]; then
  echo "smoke-edge: the edge or the stub did not come up" >&2
  docker logs "$NAME-http" >&2 || true
  docker logs "$NAME-api" >&2 || true
  exit 1
fi
echo "smoke-edge: $EDGE_IMAGE on $HTTP"

# check_spa_headers <name>: PRODUCTION_HEADERS and the edge-only headers.
check_spa_headers() {
  local name=$1 line header_name value
  while IFS= read -r line; do
    header_name=${line%%:*}
    value=${line#*: }
    check "$name: $header_name equals PRODUCTION_HEADERS" "$(header "$header_name" "$TMP/$name.headers")" "$value"
  done <<<"$expected_headers"
  check "$name: Referrer-Policy" "$(header Referrer-Policy "$TMP/$name.headers")" "strict-origin-when-cross-origin"
  local permissions
  permissions=$(header Permissions-Policy "$TMP/$name.headers")
  for feature in camera microphone geolocation payment; do
    case ", $permissions," in
      *", $feature=(),"*) pass "$name: Permissions-Policy denies $feature" ;;
      *) fail "$name: Permissions-Policy does not deny $feature: '$permissions'" ;;
    esac
  done
}

# 1. The SPA shell, its fallback and hashed assets.
check "GET / status" "$(get root "$HTTP/")" 200
if grep -q 'id="root"' "$TMP/root.body"; then pass "GET / serves index.html"; else fail "GET / does not serve index.html"; fi
check "GET / Cache-Control" "$(header Cache-Control "$TMP/root.headers")" "no-cache"
check "GET / over HTTP has no HSTS" "$(header Strict-Transport-Security "$TMP/root.headers")" ""
check_spa_headers root

check "GET /some/client/route status" "$(get fallback "$HTTP/some/client/route")" 200
if cmp -s "$TMP/root.body" "$TMP/fallback.body"; then
  pass "an unknown path falls back to index.html"
else
  fail "an unknown path does not serve index.html"
fi
check "fallback Cache-Control" "$(header Cache-Control "$TMP/fallback.headers")" "no-cache"
check_spa_headers fallback

asset=$(grep -o '/assets/[^"]*\.js' "$TMP/root.body" | head -n 1 || true)
if [ -z "$asset" ]; then
  fail "index.html references no /assets/*.js file"
else
  check "GET $asset status" "$(get asset "$HTTP$asset" -H 'Accept-Encoding: gzip')" 200
  check "asset Cache-Control" "$(header Cache-Control "$TMP/asset.headers")" "public, max-age=31536000, immutable"
  check "asset is gzip-encoded on request" "$(header Content-Encoding "$TMP/asset.headers")" "gzip"
  check_spa_headers asset
fi
check "a missing asset is 404, not index.html" "$(get missing "$HTTP/assets/missing-0000.js")" 404
check "a missing asset is not cached" "$(header Cache-Control "$TMP/missing.headers")" ""

# 2. The API proxy keeps the API's own headers and adds no SPA headers.
check "GET /api/echo status" "$(get api "$HTTP/api/echo")" 200
check "/api reaches the upstream" "$(header X-Smoke-Upstream "$TMP/api.headers")" 1
check "/api keeps the API's Cache-Control" "$(header Cache-Control "$TMP/api.headers")" "no-store"
check "/api gets no SPA Content-Security-Policy" "$(header Content-Security-Policy "$TMP/api.headers")" ""
check "/api gets no X-Frame-Options" "$(header X-Frame-Options "$TMP/api.headers")" ""
check "/api gets no Referrer-Policy" "$(header Referrer-Policy "$TMP/api.headers")" ""

# 2b. No Server header on the edge's own responses or on proxied ones: the
# stub sends Python's (#150 SR-03).
check "GET / has no Server header" "$(header Server "$TMP/root.headers")" ""
check "/api has no Server header (the upstream's is dropped)" "$(header Server "$TMP/api.headers")" ""
check "/api has no Via header" "$(header Via "$TMP/api.headers")" ""

# 2c. Request bodies are capped at 4 MB before they reach the API (#150 SR-02):
# an import-sized body (3 MB) passes whole; a 5 MB one is 413 at the edge.
head -c 3000000 /dev/zero >"$TMP/body-3mb"
head -c 5000000 /dev/zero >"$TMP/body-5mb"
check "POST /api 3 MB body status" "$(get post3 "$HTTP/api/echo" -X POST -H 'Expect:' \
  -H 'Content-Type: application/octet-stream' --data-binary "@$TMP/body-3mb" || true)" 200
check "POST /api 3 MB body reaches the upstream whole" "$(jq -r '.received' "$TMP/post3.body")" 3000000
check "POST /api 5 MB body status" "$(get post5 "$HTTP/api/echo" -X POST -H 'Expect:' \
  -H 'Content-Type: application/octet-stream' --data-binary "@$TMP/body-5mb" 2>/dev/null || true)" 413
check "POST /api 5 MB body gets no upstream answer" "$(header X-Smoke-Upstream "$TMP/post5.headers")" ""

# 3. A client-sent X-Forwarded-For is replaced by the TCP peer, the address
# Caddy logs as remote_ip in its JSON access log on stdout.
get xff "$HTTP/api/echo?xff-check" -H 'X-Forwarded-For: 1.2.3.4' >/dev/null
forwarded=$(jq -c '.x_forwarded_for' "$TMP/xff.body")
sleep 1
peer=$(docker logs "$NAME-http" 2>/dev/null |
  jq -R -r 'fromjson? | select((.logger // "" | startswith("http.log.access")) and .request.uri == "/api/echo?xff-check") | .request.remote_ip' |
  tail -n 1)
if [ -z "$peer" ]; then
  fail "no JSON access log line on stdout for /api/echo?xff-check"
else
  pass "access log: JSON on stdout (remote_ip $peer)"
  check "spoofed X-Forwarded-For is replaced by the peer" "$forwarded" "[\"$peer\"]"
fi

# 4. The notification stream arrives through the edge unbuffered and unencoded.
# The stub keeps it open for two minutes; a buffering edge shows nothing in 20 s.
start=$(date +%s)
{ curl -sS -N --max-time 20 -D "$TMP/sse.headers" -H 'Accept-Encoding: gzip, zstd' \
  "$HTTP/api/v1/notifications/stream" 2>/dev/null || true; } | grep -m 1 -q '^: heartbeat' &&
  heartbeat=true || heartbeat=false
elapsed=$(($(date +%s) - start))
if [ "$heartbeat" = true ] && [ "$elapsed" -lt 20 ]; then
  pass "SSE heartbeat through the edge after ${elapsed}s"
else
  fail "no SSE heartbeat through the edge within 20 s"
fi
check "SSE Content-Type" "$(header Content-Type "$TMP/sse.headers")" "text/event-stream"
check "SSE is not encoded" "$(header Content-Encoding "$TMP/sse.headers")" ""

# 5. Health endpoints are not reachable from outside.
for path in /health/live /health/ready; do
  check "GET $path status" "$(get health "$HTTP$path")" 404
  check "GET $path body is empty" "$(wc -c <"$TMP/health.body" | tr -d ' ')" 0
done

# 6. HTTPS (Caddy's internal CA for localhost): the same SPA headers plus HSTS.
https_status=000
for _ in $(seq 1 30); do
  https_status=$(get https "https://localhost:$HTTPS_PORT/" -k \
    --resolve "localhost:$HTTPS_PORT:127.0.0.1" 2>/dev/null || true)
  [ "$https_status" = 200 ] && break
  sleep 0.5
done
check "GET / over HTTPS status" "$https_status" 200
check "HTTPS Strict-Transport-Security" "$(header Strict-Transport-Security "$TMP/https.headers")" "max-age=31536000"
check_spa_headers https

if [ "$failures" -ne 0 ]; then
  echo "smoke-edge: $failures check(s) failed" >&2
  echo "--- edge logs ---" >&2
  docker logs "$NAME-http" >&2 || true
  exit 1
fi
echo "smoke-edge: all checks passed"
