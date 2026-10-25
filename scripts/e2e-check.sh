#!/usr/bin/env bash
# End-to-end verdict after deploying to a throwaway cluster and running the load generator:
# is the SLO met, and did every accepted message reach the archive?
# Usage: scripts/e2e-check.sh load.json
# Writes a Markdown report to $GITHUB_STEP_SUMMARY (shown on the CI run page) or to stdout.
set -euo pipefail

result=$1
report=${GITHUB_STEP_SUMMARY:-/dev/stdout}
SLO_P99_MS=200  # the SLO threshold: 99.9% of messages delivered in under 200 ms

read -r sent p50 p95 p99 < <(jq -r '[.sent, .p50_ms, .p95_ms, .p99_ms] | @tsv' "$result")
http_errors=$(jq '[.errors | to_entries[] | select(.key | startswith("http")) | .value] | add // 0' "$result")

# The archiver may need a moment to drain the last messages: wait up to 30 s.
for _ in $(seq 1 15); do
  stream=$(kubectl exec deploy/redis -c redis -- redis-cli XLEN messages)
  archived=$(kubectl exec deploy/archiver -- sh -c 'cat /data/*.jsonl | wc -l' | tr -d ' ')
  [ "$archived" -ge "$sent" ] && break
  sleep 2
done

mark() { if [ "$1" = ok ]; then echo "✅"; else echo "❌"; fi; }
slo=$(awk -v p="$p99" -v t="$SLO_P99_MS" 'BEGIN { print (p < t) ? "ok" : "ko" }')
errors=$([ "$http_errors" -eq 0 ] && echo ok || echo ko)
archive=$([ "$archived" -ge "$sent" ] && echo ok || echo ko)  # >=: at-least-once, duplicates possible

{
  echo "## End-to-end check on a throwaway Kubernetes cluster"
  echo
  echo "Commit deployed with Helm on a fresh 3-node kind cluster, then 20 simulated traders for 30 s."
  echo
  echo "| Check | Result | Target | |"
  echo "|---|---|---|---|"
  echo "| Messages accepted (\`202\`) | $sent | | |"
  echo "| HTTP errors | $http_errors | 0 | $(mark "$errors") |"
  echo "| Delivery latency p50 / p95 / p99 (client side) | $p50 / $p95 / $p99 ms | p99 < $SLO_P99_MS ms | $(mark "$slo") |"
  echo "| Archived / accepted (stream length: $stream) | $archived / $sent | nothing lost | $(mark "$archive") |"
  echo
} >> "$report"

if [ "$slo $errors $archive" = "ok ok ok" ]; then
  echo "**Verdict: ✅ SLO met, every accepted message archived.**" >> "$report"
else
  echo "**Verdict: ❌ release rejected.**" >> "$report"
  exit 1
fi
