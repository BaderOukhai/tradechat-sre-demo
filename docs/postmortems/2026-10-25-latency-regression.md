# Postmortem — Message delivery latency regression after release v1.1

| | |
|---|---|
| **Date** | 2026-10-25 (incident drill — the faulty release was injected on purpose) |
| **Duration** | 4 min (15:54:58 → 15:58:58 CET) |
| **Severity** | SEV-3 — SLO breach, no data loss |
| **Detection** | Alert `HighMessageLatencyP99` |
| **Status** | Resolved by rollback; action items open |
| **Format** | Blameless: we look for what allowed the failure, not for who caused it |

## Summary

Release v1.1 of `chat-api` (`deploy/helm/values-bad-release.yaml`) added 300 ms to the processing
of every message. The rolling update completed normally because the pods were healthy and ready;
from then on every message was delivered in ~305 ms instead of ~5 ms, above the 200 ms SLO
threshold. The latency alert fired 161 s after the deploy started, the release was rolled back in
18 s, and latency was back under 200 ms 64 s after detection.

## Impact

- **Traders:** for ~3.5 minutes, every message reached the other traders ~300 ms late
  (load generator, client side: p95 306 ms, p99 316 ms over the drill).
  3 POSTs out of 16 797 failed with a connection error during the two rolling updates (deploy and
  rollback) — the client got an error and retries, nothing was silently lost (see observations).
- **Compliance:** none. Every accepted message was archived: the compliance gap stayed at 0 and
  the archive line count equals the stream length at the end of the drill (30 596 = 30 596).
- **Error budget:** 100% of the messages sent during the incident were "bad" (> 200 ms):
  the rolling 1 h error budget went far below 0 — a real 30-day budget would have lost
  ~3.5 min worth of traffic out of the 43 min of slow messages it allows per month at 99.9%.

## Timeline (CET)

| Time | Event |
|---|---|
| 15:54:58 | Baseline: p99 5 ms, alert inactive, Helm revision 5. Deploy of v1.1 starts (`helm upgrade -f values-bad-release.yaml`). |
| 15:55:18 | Rolling update complete (20 s): all 3 pods Ready — readiness probes don't measure latency. |
| 15:55:43 | `HighMessageLatencyP99` **pending**: p99 estimated at 0.50 s (rate window of 1 min filling up). |
| 15:57:39 | Alert **firing** after its `for: 2m`. **Time to detect: 161 s.** |
| 15:57:39 | On-call checks `helm history`: a release was deployed 3 minutes earlier → prime suspect. Decision: roll back first, investigate after (runbook *HighMessageLatencyP99*, mitigation 1). |
| 15:57:57 | `helm rollback tradechat` complete (18 s, rolling update to revision 5's configuration). |
| 15:58:43 | p99 back under 200 ms (0.02 s, the 1 min rate window still contains the last slow messages). |
| 15:58:58 | Alert resolved. **Time to mitigate: 79 s. Total: 240 s.** |

## Root cause

The release introduced a fixed 300 ms delay in the message path (simulated with
`FAULT_LATENCY_MS=300`, standing for e.g. a synchronous call to a slow dependency). Nothing in the
delivery pipeline checks latency before a release reaches 100% of the pods:

- unit tests don't measure latency;
- readiness probes check that a pod *can* serve, not how fast it serves;
- the rolling update promotes a pod as soon as it is Ready.

## What went well

- The alert fired on the user-facing symptom (latency), not on a cause, and linked its runbook.
- `helm history` made the suspect obvious; one command restored service, and the rollback is a
  new, auditable revision.
- Persist-before-ack and the consumer group kept compliance intact during the whole incident.

## What went wrong

- 161 s to detect: 20 s of rollout + the 1 min rate window + the 2 min `for`. A latency regression
  shipped to 100% of the pods before anyone (or anything) looked at latency.
- The dashboard had no marker for the deploy: correlating "latency jumped" with "a release went
  out" needed `helm history`.
- `histogram_quantile` reported p99 = 0.50 s while the real latency was ~0.305 s: every sample
  fell into the (0.3, 0.5] bucket. Good enough to alert, misleading for diagnosis.

## Other observations during the drill

- **Requests cut during rollouts.** A terminating pod leaves the endpoints and receives SIGTERM at
  the same time; for a moment kube-proxy still routes to it → 3 failed POSTs. Fixed during the
  follow-up (action 7): with a 5 s `preStop`, two rolling restarts under load gave 0 HTTP errors
  on 1 800 messages.
- **Throughput dip that wasn't real.** The *received* rate dropped to ~11 msg/s during each
  rollout while the load stayed at 20 msg/s: replaced pods start new counter series and `rate()`
  needs two samples per series. The archiver (not restarted) stayed flat at 20 msg/s.
- **Noisy neighbour after recovery.** Two p99 spikes (~1 s and ~575 ms) at 15:59:30 and 16:00:30,
  p50 unaffected, came from heavy image scans run on the same machine as the cluster nodes.
  Each lasted less than the alert's `for: 2m`, so no page — which is what `for` is for.
- **Grafana OOMKilled** at 512Mi while the dashboard refreshed every 5 s: limit raised to 1Gi with
  `GOMEMLIMIT=800MiB` (the monitoring stack must survive the incidents it is meant to show).

## Action items

| # | Action | Type | Priority |
|---|---|---|---|
| 1 | Progressive delivery: canary on 1 pod with automated analysis of p99 (e.g. Argo Rollouts) before full rollout | prevent | P1 |
| 2 | Post-deploy smoke test in the pipeline: short load + p99 check, automatic rollback if > 200 ms | prevent | P1 |
| 3 | Multi-window burn-rate alerts (fast burn: 14× budget over 5 min) for faster detection of severe regressions | detect | P2 |
| 4 | Deploy annotations on the Grafana dashboard (Helm revision, image tag) | diagnose | P2 |
| 5 | Add 0.15 / 0.25 / 0.35 histogram buckets around the SLO threshold for more precise percentiles | diagnose | P3 |
| 6 | Route `warning`/`critical` alerts to the on-call tool (Alertmanager receiver), not only the UI | respond | P2 |
| 7 | `preStop` delay on chat-api so rollouts don't cut in-flight requests | prevent | **done** (verified) |
| 8 | Load generator: catch failed WebSocket handshakes during rollouts (it crashed in the follow-up test) | tooling | **done** |
