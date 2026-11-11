#!/usr/bin/env bash
# Make apt resilient to an unreachable runner mirror (#626).
# GitHub's Ubuntu runners read their package mirrors from
# /etc/apt/apt-mirrors.txt (a `mirror+file:` source). It lists the Azure
# mirror with `priority:1`, so apt keeps choosing it even when other mirrors
# are listed; when that host doesn't resolve, every apt install fails.
# Use only the main Ubuntu archive, and let apt retry transient failures.
# A no-op for the mirror list where the file doesn't exist.
set -euo pipefail

mirrors=/etc/apt/apt-mirrors.txt
if [ -f "$mirrors" ]; then
  echo "apt mirrors before:"; cat "$mirrors"
  printf 'http://archive.ubuntu.com/ubuntu/\n' | sudo tee "$mirrors" >/dev/null
  echo "apt mirrors now:"; cat "$mirrors"
fi
printf 'Acquire::Retries "5";\n' | sudo tee /etc/apt/apt.conf.d/80-retries >/dev/null
