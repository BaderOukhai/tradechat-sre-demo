# Runbook — HighMessageLatencyP99

| | |
|---|---|
| **Severity** | warning |
| **Fires when** | p99 delivery latency > 200 ms for 2 minutes |
| **SLO at risk** | 99.9% of messages delivered in under 200 ms |
| **Dashboard** | Grafana → *Messaging SLO* (`http://localhost:3000/d/messaging-slo`) |

## What it means

Traders still send and receive messages, but at least 1% of them take more than 200 ms between
reception by `chat-api` and fan-out to WebSocket clients. The error budget burns ~10× faster than
allowed. Nothing is lost: messages are persisted before the `202` and archived as usual.

## Customer impact

Trading desks see quotes and messages late. On a market-moving event, a few hundred milliseconds
matter: this is the latency the trader *feels*.

## Diagnosis

1. **Did something just change?** Most latency regressions follow a deploy.
   ```bash
   helm history tradechat
   kubectl rollout history deployment/chat-api
   ```
   A revision deployed shortly before the alert is the prime suspect.
2. **All pods or one pod?** In Prometheus (`http://localhost:9090`):
   ```promql
   histogram_quantile(0.99, sum by (le, pod) (rate(chat_message_delivery_seconds_bucket[1m])))
   ```
   - One pod slow → noisy neighbour / node issue: `kubectl get pods -o wide`, check its node.
   - All pods slow → release or shared dependency (Redis).
3. **Configuration of the running release:**
   ```bash
   kubectl get deployment chat-api -o jsonpath='{.spec.template.spec.containers[0].env}'
   ```
   (`FAULT_LATENCY_MS` > 0 is the injected fault used in the incident drill.)
4. **Saturation:** dashboard panels *CPU per pod* / *Memory per pod*; Redis health:
   ```bash
   kubectl exec deploy/redis -c redis -- redis-cli --latency -i 1 -c   # Ctrl+C after a few samples
   ```

## Mitigation

**Restore first, investigate later.**

- **Caused by a release → roll back** (one command, ~30 s with the rolling update):
  ```bash
  helm rollback tradechat            # to the previous revision
  ```
  In GitOps mode (ArgoCD), revert the commit instead: `git revert <sha> && git push`.
- **One bad pod → take it out of traffic** (keep it for investigation, it is replaced):
  ```bash
  kubectl label pod <pod> app=quarantine --overwrite
  ```
- **Saturation → scale out** (and report it in Git afterwards, or it will be reverted):
  ```bash
  kubectl scale deployment chat-api --replicas=5
  ```

Check recovery: p99 back under 200 ms on the dashboard, alert resolved in Alertmanager
(`http://localhost:9093`).

## Escalation

- Not resolved within 15 minutes, or error budget below 0 → escalate to the chat-api owner on call.
- Redis latency is the cause → escalate to the data/platform on-call.
- Open a postmortem for any SLO breach (`docs/postmortems/`).
