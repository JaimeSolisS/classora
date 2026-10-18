#!/bin/sh
# Validates the committed scanner exception lists (ADR-0016) before a scan uses them.
#
#   check-scan-exceptions.sh pip <file>    one "ID  # reason, review-by YYYY-MM-DD" per line
#   check-scan-exceptions.sh pnpm <file>   pnpm-workspace.yaml: each auditConfig.ignoreGhsas
#                                          item is "- GHSA-...  # reason, review-by YYYY-MM-DD"
#   check-scan-exceptions.sh trivy <file>  .trivyignore: each entry has exp:YYYY-MM-DD and the
#                                          line above it is a "# reason" comment
#
# Fails when an entry has no reason or no review date, or when its review date has
# passed: an exception is re-approved (new date) or removed, never kept silently.
# For "pip" it prints the IDs to ignore, one per line, on success.
set -eu

kind=$1
file=$2
today=$(date +%Y-%m-%d)

case "$kind" in
  pip | pnpm)
    awk -v kind="$kind" -v today="$today" -v file="$file" '
      function fail(msg) { print file ":" NR ": " msg ": " $0 > "/dev/stderr"; bad = 1 }
      kind == "pip" && /^[[:space:]]*(#|$)/ { next }
      kind == "pnpm" && /^[^[:space:]#]/ { in_list = ($0 ~ /^auditConfig:/) ; next }
      kind == "pnpm" && in_list && /^[[:space:]]+ignoreGhsas:/ { in_ghsas = 1; next }
      kind == "pnpm" && in_list && /^[[:space:]]+[A-Za-z]+:/ { in_ghsas = 0; next }
      kind == "pnpm" && !(in_list && in_ghsas && /^[[:space:]]*-/) { next }
      {
        line = $0
        sub(/^[[:space:]]*-?[[:space:]]*/, "", line)
        id = line
        sub(/[[:space:]].*$/, "", id)
        if (index(line, "#") == 0) { fail("exception without a reason comment"); next }
        comment = substr(line, index(line, "#") + 1)
        if (match(comment, /review-by [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/) == 0) {
          fail("exception without \"review-by YYYY-MM-DD\""); next
        }
        date = substr(comment, RSTART + 10, 10)
        reason = substr(comment, 1, RSTART - 1)
        gsub(/[[:space:],]/, "", reason)
        if (reason == "") { fail("exception without a reason"); next }
        if (date < today) { fail("review date " date " has passed (re-approve with a new date or remove it)"); next }
        ids[++n] = id
      }
      END {
        if (bad) exit 1
        if (kind == "pip") for (i = 1; i <= n; i++) print ids[i]
      }
    ' "$file"
    ;;
  trivy)
    awk -v today="$today" -v file="$file" '
      function fail(msg) { print file ":" NR ": " msg ": " $0 > "/dev/stderr"; bad = 1 }
      /^[[:space:]]*#/ { prev_comment = ($0 ~ /^[[:space:]]*#[[:space:]]*[^[:space:]]/); next }
      /^[[:space:]]*$/ { prev_comment = 0; next }
      {
        if (!prev_comment) fail("exception without a \"# reason\" comment on the line above")
        else if (match($0, /exp:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]/) == 0) fail("exception without exp:YYYY-MM-DD")
        else if (substr($0, RSTART + 4, 10) < today) fail("exp date has passed (re-approve with a new date or remove it)")
        prev_comment = 0
      }
      END { if (bad) exit 1 }
    ' "$file"
    ;;
  *)
    echo "usage: $0 pip|pnpm|trivy <file>" >&2
    exit 2
    ;;
esac
