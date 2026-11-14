#!/usr/bin/env bash
# Make apt survive one unreachable Ubuntu mirror on GitHub runners.
#
# History:
# - The runners read their package mirrors from /etc/apt/apt-mirrors.txt (a
#   `mirror+file:` source). The stock list puts the Azure mirror at
#   `priority:1`. On 2026-11-11 the runners could resolve no *.ubuntu.com host
#   at all, so every apt install failed (#626).
# - #626 then pinned the list to ONLY mirrors.edge.kernel.org and pointed the
#   security source at it too. On 2026-11-14 that host was unreachable from the
#   runners (IPv6 "Network is unreachable", IPv4 "Could not connect"), and every
#   job that installs OS packages failed again (#685).
#
# Design (#685): no single host is a point of failure.
# - The mirror list holds several full official mirrors, in different DNS
#   domains and networks. apt-transport-mirror tries them in ascending
#   `priority:N` order (one URL per line, metadata after a tab) and moves to
#   the next mirror for any file the current one fails to serve.
# - The security pocket (noble-security) goes through the same list rather than
#   its own security.ubuntu.com source: every full mirror carries it, and a
#   lone security.ubuntu.com would fail with the rest of *.ubuntu.com as on
#   2026-11-11.
# - apt does NOT remember a dead mirror: every file (about 30 per update)
#   starts at the first mirror again, and Acquire::Retries retries it there
#   with a growing delay first. A black-holed first mirror (connect never
#   answers) hung `apt-get update` for 45+ minutes in a local test. So the
#   script first probes each mirror (IPv4, 5 s cap) and lists only the ones
#   that answer, in order; if none answers it keeps them all.
# - IPv4 only (the runners have no IPv6 route), a 10 s timeout, and 2 retries
#   (not 5) for transient errors. A mirror that dies between the probe and
#   apt still costs ~30 s per file; the probe keeps that window to seconds.
# A no-op for the sources where /etc/apt/apt-mirrors.txt doesn't exist.
set -euo pipefail

mirrors=/etc/apt/apt-mirrors.txt
if [ -f "$mirrors" ]; then
  echo "apt mirrors before:"; cat "$mirrors"
  candidates=(
    http://archive.ubuntu.com/ubuntu/
    http://azure.archive.ubuntu.com/ubuntu/
    http://mirrors.edge.kernel.org/ubuntu/
    http://mirror.math.princeton.edu/pub/ubuntu/
  )
  codename=$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release)
  live=()
  for m in "${candidates[@]}"; do
    if curl -4 -fsSI --max-time 5 -o /dev/null "${m}dists/${codename}/InRelease"; then
      live+=("$m")
    else
      echo "skipping unreachable mirror $m"
    fi
  done
  [ "${#live[@]}" -gt 0 ] || live=("${candidates[@]}")
  for i in "${!live[@]}"; do
    printf '%s\tpriority:%s\n' "${live[$i]}" "$((i + 1))"
  done | sudo tee "$mirrors" >/dev/null
  echo "apt mirrors now:"; cat "$mirrors"
  # Route the separate security source through the same list.
  for f in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
    [ -f "$f" ] || continue
    if grep -Eq 'https?://security\.ubuntu\.com/ubuntu' "$f"; then
      sudo sed -E -i "s#https?://security\.ubuntu\.com/ubuntu/?#mirror+file:${mirrors}#g" "$f"
      echo "security source now uses the mirror list in $f"
    fi
  done
fi
printf '%s\n' \
  'Acquire::ForceIPv4 "true";' \
  'Acquire::http::Timeout "10";' \
  'Acquire::Retries "2";' \
  | sudo tee /etc/apt/apt.conf.d/80-retries >/dev/null
