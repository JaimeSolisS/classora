#!/usr/bin/env bash
# Make apt resilient to an unreachable runner mirror (#626).
# GitHub's Ubuntu runners read their package mirrors from
# /etc/apt/apt-mirrors.txt (a `mirror+file:` source), which lists only the
# Azure mirror. When that host doesn't resolve, every apt install fails.
# Put the main Ubuntu archive first and keep the existing entries after it as
# fallbacks (apt tries the mirrors in order), and let apt retry transient
# failures. A no-op for the mirror list where the file doesn't exist.
set -euo pipefail

mirrors=/etc/apt/apt-mirrors.txt
main=http://archive.ubuntu.com/ubuntu/
if [ -f "$mirrors" ]; then
  others=$(grep -v -x -F "$main" "$mirrors" | grep -v '^[[:space:]]*$' || true)
  printf '%s\n%s\n' "$main" "$others" | sudo tee "$mirrors" >/dev/null
  echo "apt mirrors:"; cat "$mirrors"
fi
printf 'Acquire::Retries "5";\n' | sudo tee /etc/apt/apt.conf.d/80-retries >/dev/null
