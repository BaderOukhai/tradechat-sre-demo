#!/usr/bin/env bash
# Incident drill: ship a bad release, wait for the alert, roll back, wait for recovery.
# Prints a timestamped timeline (the raw material of the postmortem).
# Needs traffic: run "make load ARGS='--duration 600'" in another terminal first.
set -euo pipefail

PROM=http://localhost:9090
ALERT=HighMessageLatencyP99

log() { echo "$(date +%H:%M:%S)  $*"; }
query() { curl -s --data-urlencode "query=$1" "$PROM/api/v1/query" | jq -r '.data.result[0].value[1] // "0"'; }
p99() { query 'histogram_quantile(0.99, sum by (le) (rate(chat_message_delivery_seconds_bucket[1m])))'; }
alert_state() {
  curl -s "$PROM/api/v1/alerts" |
    jq -r --arg a "$ALERT" '[.data.alerts[] | select(.labels.alertname == $a) | .state][0] // "inactive"'
}
above() { awk -v v="$1" -v t="$2" 'BEGIN { exit !(v > t) }'; }  # float comparison: v > t ?

if ! above "$(query 'sum(rate(chat_messages_received_total[1m]))')" 1; then
  echo "No traffic: run \"make load ARGS='--duration 600'\" in another terminal first." >&2
  exit 1
fi

log "BASELINE   p99=$(p99)s  alert=$(alert_state)  helm revision=$(helm history tradechat --max 1 -o json | jq '.[0].revision')"
start=$(date +%s)

log "DEPLOY     bad release v1.1 (FAULT_LATENCY_MS=300), rolling update..."
helm upgrade tradechat deploy/helm/tradechat -f deploy/helm/values-bad-release.yaml --wait --timeout 3m >/dev/null
log "DEPLOYED   rollout complete, p99=$(p99)s"

state=""
until [ "$state" = "firing" ]; do
  sleep 5
  new=$(alert_state)
  [ "$new" != "$state" ] && log "ALERT      $ALERT is $new  (p99=$(p99)s)"
  state=$new
done
detected=$(date +%s)
log "DETECTED   time to detect: $((detected - start)) s after the deploy started"

log "MITIGATE   helm rollback (restore first, investigate later)"
helm rollback tradechat --wait --timeout 3m >/dev/null
log "ROLLED BACK"

until ! above "$(p99)" 0.2; do sleep 5; done
log "RECOVERED  p99=$(p99)s, back under 200 ms"
until [ "$(alert_state)" = "inactive" ]; do sleep 5; done
resolved=$(date +%s)
log "RESOLVED   alert cleared"

echo
echo "Time to detect:   $((detected - start)) s"
echo "Time to mitigate: $((resolved - detected)) s (alert firing -> resolved)"
echo "Total impact:     $((resolved - start)) s"
