# Runbook — PodCrashLooping

| | |
|---|---|
| **Severity** | warning (critical if it is the archiver or Redis, see impact) |
| **Fires when** | a container of the release has been in `CrashLoopBackOff` for 1 minute |

## What it means

A container keeps starting and dying; Kubernetes restarts it with an increasing delay (up to
5 minutes). It never fixes itself.

## Customer impact — depends on the pod

- **chat-api, during a rollout:** usually none. With `maxUnavailable: 0` the rolling update waits
  for the new pod to be Ready, so the old pods keep serving and the bad release is blocked.
- **archiver:** messages stop being archived → `ComplianceArchiveGap` follows. Treat as critical.
- **redis:** chat-api becomes not ready, POSTs get `503`. Treat as critical.

## Diagnosis

1. **Which pod, which container, why did it die?**
   ```bash
   kubectl get pods -o wide
   kubectl describe pod <pod>      # "Last State: Terminated", "Reason", "Exit Code", Events
   ```
   | Reason / exit code | Usual cause |
   |---|---|
   | `OOMKilled` / 137 | memory limit too low, or a memory leak |
   | `Error` / 1 | application error at startup: read the logs |
   | `CreateContainerConfigError` | missing ConfigMap / Secret / bad env |
   | liveness probe failures in Events | app too slow to answer, or probe misconfigured |
2. **Logs of the crashed instance** (the previous container, not the restarting one):
   ```bash
   kubectl logs <pod> --previous
   ```
3. **Did a release just roll out?**
   ```bash
   helm history tradechat
   kubectl get replicasets -l app=chat-api      # a new ReplicaSet stuck below its desired count
   ```

## Mitigation

- **Caused by a release → roll back:**
  ```bash
  helm rollback tradechat
  ```
- **OOMKilled with a legitimate usage increase** → raise the memory limit in `values.yaml`
  (based on measured usage, see the *Memory per pod* panel), commit, redeploy.
- **Single bad pod on a bad node** → `kubectl delete pod <pod>` (rescheduled elsewhere) and
  `kubectl cordon <node>` while investigating the node.

Recovery check: `kubectl get pods` all `Running` with stable `RESTARTS`, alert resolved.

## Escalation

- archiver or Redis crash looping → escalate immediately (compliance / availability).
- chat-api crash looping outside a rollout → escalate to the service owner.
