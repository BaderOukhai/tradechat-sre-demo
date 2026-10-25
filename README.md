# tradechat-sre-demo

A miniature of a **financial messaging platform** — real-time chat between traders where **every
message must be archived** (banks have a legal obligation to retain trader communications) —
operated the way an SRE team would run it: SLOs, dashboards, alerts, runbooks, an incident,
a rollback and a blameless postmortem. Everything runs locally on a 3-node Kubernetes cluster,
at zero cloud cost.

![Messaging SLO dashboard: the archiver is stopped under load, the compliance gap grows to 1 250 messages, then drains in seconds when it comes back](docs/assets/dashboard-compliance.png)

*The archiver is stopped under load: the compliance gap grows by 20 messages per second, the critical alert fires, and the backlog drains in seconds once the archiver is back — nothing lost.*

## What it demonstrates

| Promise | How |
|---|---|
| **Real-time** | FastAPI + WebSockets; p99 delivery ~12–17 ms under load (SLO: 99.9% < 200 ms) |
| **Nothing lost** | a message is persisted to a Redis Stream (AOF on a volume) **before** the `202` |
| **Everything archived** | archiver on a consumer group: write + `fsync`, **then** `XACK` (at-least-once) |
| **Resilient** | 3 replicas spread over nodes, rolling updates with `maxUnavailable: 0`, PDB, probes |
| **Observable** | Prometheus + Grafana: latency, traffic, errors, saturation, SLI, error budget, compliance gap |
| **Operable** | 3 alerts with runbooks, a scripted incident drill, rollback, postmortem |
| **Secure by default** | non-root images, read-only filesystem, no capabilities, Trivy scan in CI |
| **AI-assisted ops, safely** | a read-only, audited MCP server so an assistant can answer *"is the platform healthy?"* |

## Architecture

```mermaid
flowchart LR
    T["Traders<br/>(loadgen)"] -- "POST /messages<br/>WebSocket /ws/room" --> SVC["Service chat-api<br/>(NodePort)"]
    SVC --> C["chat-api ×3<br/>FastAPI"]
    C -- "XADD, then 202" --> R[("Redis Stream<br/>AOF + PVC")]
    R -- "XREAD: fan-out<br/>to every replica" --> C
    R -- "XREADGROUP" --> A["archiver<br/>consumer group"]
    A -- "JSONL + fsync,<br/>then XACK" --> V[("Archive<br/>PVC")]
    R -.- E["redis_exporter<br/>(sidecar)"]
    P["Prometheus"] -. scrape .-> C & A & E
    P --> G["Grafana<br/>Messaging SLO"]
    P --> AM["Alertmanager"] --> RB["Runbooks"]
    AG["ops-agent (MCP)<br/>read-only, audited"] -. query .-> P & AM
```

- **chat-api** persists each message (`XADD`) before answering `202`, then every replica tails the
  stream (`XREAD`) and pushes messages to *its own* WebSocket clients: a trader connected to pod A
  receives a message posted to pod B.
- **archiver** reads the same stream through a **consumer group** (each message to one archiver,
  Redis keeps the bookmark and the pending list): write + `fsync`, then `XACK`. A crash means a
  re-delivery (duplicate, deduplicated by id), never a loss; `XAUTOCLAIM` takes over messages a
  dead archiver left unacknowledged.
- **Compliance gap** = consumer group lag + pending, measured at the source by `redis_exporter`,
  independently of the archiver it watches.

## Quickstart

Prerequisites: Docker (Desktop or OrbStack, ~6 GB RAM), `kind`, `kubectl`, `helm`, `uv`, Python 3.12.

```bash
make tools                               # check prerequisites
make cluster images monitoring deploy    # kind cluster, images, Prometheus/Grafana, the app (~3 min)
make load                                # 20 simulated traders for 2 minutes, prints p50/p95/p99
make urls                                # Grafana, Prometheus, Alertmanager URLs + Grafana password
```

| URL | |
|---|---|
| http://localhost:3000/d/messaging-slo | Grafana dashboard (anonymous, read-only) |
| http://localhost:9090 | Prometheus |
| http://localhost:9093 | Alertmanager |
| http://localhost:8000 | chat-api (`POST /messages`, `GET /ws/{room}`, `/healthz`, `/readyz`, `/metrics`) |

Faster dev loop without Kubernetes: `make up` (docker compose) and `make down`.

## Demo scenarios

| Scenario | Command | What you see |
|---|---|---|
| **Pod failure** | `kubectl delete pod <chat-api pod>` during `make load` | short dip in ready pods ([screenshot](docs/assets/dashboard-resilience.png)); measured: 0 HTTP errors, 2 399 / 2 399 messages archived, p99 11.9 ms |
| **Incident drill** | `make load ARGS="--duration 600"` then `make incident` | bad release (+300 ms) → `HighMessageLatencyP99` fires → `helm rollback` → recovery, with a timeline |
| **Compliance** | `kubectl scale deploy archiver --replicas=0` during load | gap grows 20 msg/s, `ComplianceArchiveGap` (critical) fires; scale back → backlog drained in < 15 s |
| **Bad release blocked** | `helm upgrade tradechat deploy/helm/tradechat --set chatApi.resources.limits.memory=40Mi --set chatApi.resources.requests.memory=32Mi` | new pod OOMKilled, rollout stuck, old pods keep serving, `PodCrashLooping` fires |
| **Ask the ops agent** | Claude Code at the repo root: *"Is the messaging platform healthy? What should I do?"* | alerts + SLO + compliance gap + the runbook, every call in `ops-agent/audit.log` |

The full live demo (3 minutes) is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) (in French).

![Incident drill: bad release, p99 jumps to ~500 ms, alert fires, rollback, recovery](docs/assets/incident.gif)

| Incident drill (bad release → rollback) | Alertmanager during the incident |
|---|---|
| ![](docs/assets/dashboard-incident.png) | ![](docs/assets/alertmanager-firing.png) |

Measured: **time to detect 161 s, time to mitigate 79 s**, 0 messages lost — see the [postmortem](docs/postmortems/2026-10-25-latency-regression.md).

## SLO and error budget

- **SLO:** 99.9% of messages delivered (reception → WebSocket fan-out) in under **200 ms**.
- **SLI** = good / total = `rate(chat_message_delivery_seconds_bucket{le="0.2"}) / rate(chat_message_delivery_seconds_count)` — which is why `0.2` is a histogram bucket boundary.
- **Error budget** = 1 − SLO = 0.1% of messages. Remaining = `1 − (bad / total) / 0.001`
  (rolling 1 h window in the demo, 30 days in production).
- **Why 200 ms:** the order of magnitude at which a trader notices a late message; normal p99 is
  ~5–17 ms, so the alert only fires on a real regression.

## Alerts

| Alert | Condition | Severity | Runbook |
|---|---|---|---|
| `HighMessageLatencyP99` | p99 > 200 ms for 2 min | warning | [runbook](docs/runbooks/HighMessageLatencyP99.md) |
| `ComplianceArchiveGap` | messages not archived (or not measurable) for 1 min | **critical** | [runbook](docs/runbooks/ComplianceArchiveGap.md) |
| `PodCrashLooping` | a container in CrashLoopBackOff for 1 min | warning | [runbook](docs/runbooks/PodCrashLooping.md) |

Compliance is critical and latency only a warning: a slow chat degrades the experience, an
incomplete archive is a potential regulatory breach for the customer bank.
Incident write-up: [docs/postmortems/](docs/postmortems/).

## Repository layout

```
apps/chat-api/          FastAPI service: POST /messages, WebSocket fan-out, probes, metrics, fault injection
apps/archiver/          consumer-group archiver: JSONL + fsync, then XACK
loadgen/                simulated traders (asyncio), client-side latency percentiles
deploy/kind-config.yaml 1 control-plane + 2 workers, NodePorts mapped to localhost
deploy/helm/tradechat/  the app chart: Deployments, Services, PVCs, PDB, ServiceMonitors, alerts, dashboard
deploy/monitoring/      trimmed kube-prometheus-stack values
deploy/argocd/          GitOps: Argo CD values + Application (optional, make gitops)
deploy/basics/          plain manifests used to learn the Kubernetes building blocks (step 6a)
terraform/              the whole environment as code (kind + Helm providers)
ops-agent/              read-only MCP server for on-call (SLO, alerts, runbooks, compliance gap)
scripts/incident.sh     incident drill with a timeline
docs/                   runbooks, postmortem, CI notes, demo script, screenshots
```

## Design decisions & trade-offs

- **Redis Streams, not Kafka.** One stream is both the durable log and the fan-out bus, and the
  consumer group gives at-least-once archiving. Kafka would be the production choice at scale
  (partitioning, replication); Redis keeps the demo small. Single Redis = a known single point of
  failure (mitigated here with AOF on a persistent volume only).
- **At-least-once, not exactly-once.** ACK after write: a crash can duplicate, never lose.
  Duplicates are removed by message id. In compliance, losing is illegal, duplicating is annoying.
- **Count a message as received only after `XADD`.** A `503` tells the client to retry; the
  received counter never includes messages that weren't persisted.
- **Compliance gap from Redis, not from counters.** `received − archived` from two counters gave a
  false alert on every pod restart (counters restart at 0). Lag + pending of the consumer group is
  the source of truth and survives restarts.
- **Liveness never checks Redis.** A Redis outage must take pods out of traffic (readiness), not
  restart all of them (cascading CrashLoopBackOff).
- **No CPU limit on chat-api.** Memory limits (incompressible, OOMKill) yes; CPU throttling would
  add latency and eat the SLO. Requests and limits come from measured usage.
- **Topology spread, not hard anti-affinity.** 3 replicas on 2 workers: a hard rule would leave a
  pod Pending forever; `maxSkew: 1` guarantees 2+1.
- **`maxUnavailable: 0`.** A release whose pods never become Ready can't take capacity away: the
  rollout blocks itself (verified with an OOMKilled release).
- **NodePort instead of `kubectl port-forward`.** A port-forward is pinned to one pod and dies with
  it; through the Service, killing a pod is invisible to clients.
- **The service ships its own observability.** ServiceMonitors, alerts and the dashboard live in
  the app chart, versioned with the code ("you build it, you own it").
- **Archive volumes survive `helm uninstall`** (`helm.sh/resource-policy: keep`).

## How this was built

Built with an AI coding assistant (**Claude Code**), as a deliberate learning project. The working
rules I gave it: small steps, explain the *why* before coding, every configuration value justified
in a comment, one commit per step, and **I** run the learning commands (`docker`, `kubectl`,
`helm`, `kind`) myself.

How I made sure I understand it rather than just own it:
- after each step, interview-style questions answered without notes, then corrected — kept as
  private study notes, wrong answers included;
- every claim is verified by running it: tests, a pod killed under load, alerts triggered on
  purpose, an OOMKilled release, a full incident drill with a measured timeline, the whole
  environment rebuilt with `terraform apply`, the MCP agent queried during a live incident;
- the bugs found along the way are documented with their diagnosis: a stale Redis connection after
  a restart, a counter reset faking a compliance gap, `helm upgrade` silently reverting a manual
  scale, an alert still firing 5 minutes after the fix, requests cut during rolling updates,
  Grafana OOMKilled, and a kubeconfig (with the cluster's private key) caught before it was pushed.

## What I'd do in production

- **Security:** mTLS between services (service mesh), secrets in a KMS / Vault, NetworkPolicies,
  signed images and admission control, hardened Redis image, end-to-end encryption with keys held
  by the customer (the key manager and encrypting agent on the customer's premises).
- **Data:** Kafka or replicated Redis instead of a single Redis; the archive on immutable
  (WORM) object storage with retention policies and legal holds; deduplication by message id at
  export time.
- **Availability:** multi-zone then multi-region, HPA on chat-api, a client that re-fetches history
  from its last message id after a reconnect.
- **SLOs:** per-customer SLOs, 30-day windows, multi-window burn-rate alerts, alert routing to an
  on-call tool, load tests in CI.
- **Delivery:** GitOps (Argo CD) as the only path to production, progressive delivery (canary),
  Terraform with a remote, locked state.

## What I learned

- Measure at the source: counters restart, the system of record doesn't.
- Test alerts by triggering them — two of mine were wrong until I did.
- A manual fix during an incident isn't done until it's in Git: the next deploy reverts it.
- `histogram_quantile` estimates inside a bucket: with a 300 ms fault, p99 showed ~500 ms
  because every sample fell in the (0.3, 0.5] bucket.
- Restore first, understand later — and write it down without blame.

## More

- [docs/ci.md](docs/ci.md) — CI pipeline (GitHub Actions + Jenkinsfile), comparison with Cloud Build
- [terraform/README.md](terraform/README.md) — state, plan/apply, drift
- [ops-agent/README.md](ops-agent/README.md) — the MCP server and its guardrails
- [deploy/argocd/](deploy/argocd/) — GitOps with Argo CD: `make gitops` (the repository must be public, or Argo CD given read credentials), rollback with `git revert`
