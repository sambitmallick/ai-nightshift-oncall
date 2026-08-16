# Postmortem: `orders-worker` v2.4.0 Deploy Failure — Failed Database Migration 0042

**Date:** 2026-08-16 · **Severity:** SEV-2 (single service fully down, ongoing at time of writing) · **Status:** Open — schema repair in progress
**Services affected:** `orders-worker` (order processing), shared orders database
**All timestamps UTC** (node-local output in the `kubectl describe` artifact is IST, UTC+05:30).

---

## Summary

At 14:43:41 UTC, `orders-worker` v2.4.0 was deployed as a fresh, single-replica Deployment (revision 1). The container runs database migration `0042_add_orders_index` as part of its startup command; the migration failed halfway with `duplicate key value violates unique constraint orders_pkey`, leaving the schema in an inconsistent state, and the container exited with code 1. Kubernetes' `restartPolicy: Always` then re-ran the failed migration against the already-inconsistent schema on every restart (4 attempts in ~100 seconds), producing a CrashLoopBackOff. Order processing was fully unavailable from the moment of deploy, and recovery is blocked on manual database repair — no Kubernetes-level action can resolve it.

## Impact

- **Order processing: 100% down.** The Deployment runs a single replica (`spec.replicas: 1`), and that pod never reached Ready (`0/1`, `Ready: False`, `ContainersReady: False`). The Deployment reported `Available: False` / `MinimumReplicasUnavailable` at 14:44:43. No orders were processed from 14:43:41 onward; the outage was still ongoing at the last evidence point (14:45:44) and will persist until the schema is manually repaired.
- **Database schema left inconsistent.** The application's own log states: `panic: database schema is now in an inconsistent state, manual intervention required`. Any other service sharing the orders database is at risk until migration 0042 is repaired or rolled back.
- **Repeated writes against a broken schema.** The migration was re-executed 4 times (initial start + 3 restarts, confirmed by `Restart Count: 3` and the `Pulled`/`Created`/`Started` events showing `x4 over 102s`), each attempt running against a schema already known to be half-migrated.
- **Customer impact (inferred):** orders submitted during the outage window were queued or failed rather than processed. Duration of customer impact equals the time until manual DBA remediation completes.

## Detection & Timeline

| Time (UTC) | Event | Evidence |
|---|---|---|
| 14:43:41 | Deployment `orders-worker` v2.4.0 applied as **revision 1** (first-ever deploy of this Deployment — no prior ReplicaSet exists to roll back to). Pod `orders-worker-6bb6b69974-7l8vx` scheduled to `nightshift-control-plane`. | Deployment `creationTimestamp`, `deployment.kubernetes.io/revision: "1"`; `Scheduled` event; pod Start Time 20:13:41 IST |
| ~14:43:41–14:43:49 | Attempt 1: container starts, logs `orders-worker v2.4.0 starting` and `applying database migration 0042_add_orders_index...`, then fails: `ERROR: migration 0042 failed halfway: duplicate key value violates unique constraint orders_pkey`. Exit code 1. | Container logs; describe `Exit Code: 1` |
| 14:43:49–14:44:38 | Attempts 2–3: kubelet restarts the container per `restartPolicy: Always`; each restart re-runs migration 0042 and fails identically. First `BackOff` warning ~14:43:51 (`x3 over 92s`). Attempt 3 ran 14:44:04–14:44:08. | Events (`Started x4`, `BackOff x3`); describe `Last State: Terminated` 20:14:04–20:14:08 IST |
| 14:44:43 | Deployment condition flips to `Available: False`, reason `MinimumReplicasUnavailable`. | Deployment `status.conditions` |
| 14:44:38–14:44:42 | Attempt 4 runs and exits 1. Pod enters sustained CrashLoopBackOff. | describe `State: Terminated` 20:14:38–20:14:42 IST |
| 14:45:28 | **Detection:** Alertmanager fires `PodCrashLooping` (severity: warning) for the pod. Time-to-detect: ~1m47s from deploy. | `06-alert.txt`, `startsAt: 14:45:28.901Z` |
| 14:45:31 | Automated on-call agent receives the alert. | Agent log |
| 14:45:44 | **Escalation:** agent pages a human after 13 seconds of triage. It correctly declines to restart/delete the pod or roll back, reasoning that any restart re-runs the failed migration against inconsistent persistent state and that "no kubectl-level change can fix a database schema problem." (The page was emitted twice — see What went poorly.) | Agent log, two identical `ESCALATE` entries at 14:45:44 |
| 14:45:44 → ongoing | Incident open; awaiting manual repair or rollback of migration 0042 by a DBA/engineer. | — |

## Root Cause Analysis (5 Whys)

1. **Why was order processing down?**
   The only `orders-worker` replica was in CrashLoopBackOff and never became Ready (`0/1`, `Restart Count: 3`), so the Deployment had zero available pods (`MinimumReplicasUnavailable` at 14:44:43).

2. **Why was the pod crash-looping?**
   The container exited with code 1 on every start (describe: `State: Terminated, Reason: Error, Exit Code: 1` for both current and last state), and `restartPolicy: Always` kept restarting it into the same failure.

3. **Why did the container exit on every start?**
   Startup runs database migration `0042_add_orders_index`, which failed with `duplicate key value violates unique constraint orders_pkey` and panicked: `database schema is now in an inconsistent state, manual intervention required` (container logs).

4. **Why did the migration fail and leave the schema inconsistent — and why did it keep re-running?**
   The migration was neither atomic nor idempotent: it "failed halfway," meaning part of it committed before the duplicate-key error, and re-running it against the half-applied schema fails again rather than resuming or no-oping. It kept re-running because the migration is embedded directly in the container's startup command (`spec.template.spec.containers[0].args` in the Deployment manifest runs the migration inline before the worker starts), so every kubelet restart is a fresh migration attempt.

5. **Why was a non-atomic, non-idempotent migration in the application startup path of a single-replica Deployment?**
   There is no separation between schema changes and application deployment: no dedicated migration Job, no run-once guard or migration lock, no pre-deploy validation of the migration against production-like data (which would have surfaced the `orders_pkey` conflict before shipping), and no deploy gate — the Deployment even reported `Progressing: True / NewReplicaSetAvailable` at 14:43:42 while the pod was already failing. The deployment pipeline treated "manifest applied" as success and left correctness to runtime.

**Root cause:** `orders-worker` v2.4.0 executed a non-atomic, non-idempotent schema migration inline in the container startup command of a single-replica Deployment. When the migration hit a data conflict (`orders_pkey` duplicate) it half-applied and crashed the only replica, and Kubernetes' automatic restart behavior repeatedly re-ran the failed migration against the now-inconsistent schema — converting one bad migration into a full, self-perpetuating service outage that no orchestrator-level action could fix.

## What went well

- **Detection was fast.** Alertmanager fired `PodCrashLooping` at 14:45:28, ~107 seconds after the deploy landed — before most humans would have noticed.
- **The automated on-call agent made the right call under pressure, in 13 seconds.** It recognized that the failure involved partially-applied persistent state, explicitly reasoned that restarting/deleting the pod would re-run the failed migration and that rollback could hide the half-applied change, and escalated to a human instead of "fixing" the symptom. Given that this was revision 1 (no previous ReplicaSet), a reflexive `kubectl rollout undo` would have failed or made things murkier — the agent's restraint prevented additional harm.
- **The application failed loudly and legibly.** The log lines named the exact migration (`0042_add_orders_index`), the exact constraint (`orders_pkey`), and stated `manual intervention required` — triage required no log archaeology.
- **CrashLoopBackOff throttling limited damage.** Exponential backoff capped the failed migration at 4 executions in the observed window rather than a tight retry loop hammering the database.

## What went poorly

- **Schema migration ran in the app startup path** (`command: /bin/sh -c` with the migration inline in `args`), coupling database state changes to pod lifecycle. Every one of the 4 container starts re-executed the migration against a schema already known to be inconsistent.
- **The migration was not atomic and not idempotent.** "Failed halfway" plus a repeatable failure on retry means partial changes committed outside a transaction with no resume/no-op path.
- **The `orders_pkey` conflict indicates the migration was not validated against production-like data** before shipping.
- **Single replica, zero resilience headroom.** `replicas: 1` means any pod-level failure is a total outage. The pod is also `QoS Class: BestEffort` (no resource requests/limits) and has **no readiness or liveness probes**, so even a healthy rollout would have had no gate between "container started" and "taking traffic."
- **The deploy pipeline had no health gate.** The Deployment recorded `NewReplicaSetAvailable` / "successfully progressed" at 14:44:42 while the pod was crash-looping; nothing halted or flagged the rollout — detection depended entirely on the downstream Alertmanager rule.
- **The escalation page was emitted twice** (two identical `ESCALATE` entries at 14:45:44) — harmless here, but duplicate pages erode pager trust and could mask a second, distinct incident.
- **Recovery requires manual intervention with no documented runbook** for half-applied migrations, extending the outage window.

## Action Items

| Pri | Action | Owner (role) | Prevents |
|---|---|---|---|
| **P0** | Repair or roll back migration 0042: assess which statements committed, restore `orders` schema to a consistent state, verify against the migration ledger, then unblock the rollout. Document every step taken for the runbook (see P2). | DBA + orders-worker service owner | Ongoing outage; silent schema drift affecting other consumers of the orders DB |
| **P0** | Remove migrations from the container startup command. Run schema migrations as a separate, explicitly-invoked pre-deploy step (e.g., a Kubernetes Job or CI stage) that must succeed before the new app version rolls out. | Backend (orders team) | Crash-loops re-executing migrations; app availability being hostage to migration outcomes |
| **P0** | Require migrations to be transactional where the database supports it, and idempotent/resumable where it doesn't (guard clauses, `IF NOT EXISTS`, migration ledger check before execute). Enforce via migration-framework lint in CI. | Backend + DBA | "Failed halfway" states; retries compounding damage on inconsistent schemas |
| **P1** | Add a pre-merge migration validation stage that runs every migration against a production-like data snapshot. Migration 0042's `orders_pkey` duplicate would have been caught here. | Data platform / CI owners | Data-dependent migration failures reaching production |
| **P1** | Raise `orders-worker` to ≥2 replicas with a PodDisruptionBudget, add readiness and liveness probes, and set resource requests/limits (currently BestEffort). | Platform / SRE | Single-pod failures becoming total outages; unqualified pods taking traffic; eviction under node pressure |
| **P1** | Gate rollouts on pod health, not manifest application: fail the deploy pipeline if the new ReplicaSet doesn't reach Ready within a deadline (rollout status check or progressive-delivery tooling), and page the deploy owner directly. | Platform / SRE | ~105-second gap where a fully-down deploy was only caught by a generic CrashLoop alert |
| **P2** | Write and drill a runbook for partially-applied migrations: how to determine what committed, safe repair vs. rollback criteria, and who has authority to modify prod schema during an incident. Seed it from the P0 remediation notes. | SRE + DBA | Extended MTTR the next time a migration half-applies |
| **P2** | Deduplicate on-call agent escalations (idempotency key per alert fingerprint) — it paged twice at 14:45:44 for one alert. | SRE tooling | Pager noise and masking of concurrent distinct incidents |
| **P2** | Add database-side alerting on migration failures and schema-consistency checks, so DB state problems page the DBA path directly instead of arriving disguised as a Kubernetes pod alert. | Observability + DBA | Slow or misrouted triage when the root cause is in the data layer, not the orchestrator |

---

*This postmortem is blameless. The engineers who shipped v2.4.0 followed the deployment process as it existed; the process allowed an unvalidated, non-atomic migration to run inline at container startup with no gate. The action items above fix the process, not the people.*
