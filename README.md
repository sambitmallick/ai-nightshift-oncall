# AI Night-Shift On-Call — auto-heal the safe Kubernetes incidents, escalate the rest

Companion to the DevOps Autopilot episode **"I Let an AI Run My Kubernetes On-Call at
3am. It Healed 4 Outages and Refused the 5th."** Everything here runs locally on a
laptop, for **$0** (Claude on a normal subscription, no API bill).

> ⚠️ **This is a safe, local demo on a throwaway cluster.** Auto-remediation against
> real production needs far more care than one repo. Treat the allow-list idea as a
> starting point, not a drop-in.

## The idea

An AI agent sits on-call. When a real Prometheus alert fires, it:

1. **Gathers the ground truth** — `kubectl describe`, `logs`, `get events`, and the
   owning Deployment. It does not guess; it reads the live cluster.
2. **Asks Claude** to name the root cause and classify the fix as **SAFE** (a
   reversible config change) or **RISKY** (needs a human).
3. **Enforces a guard-rail** — and this is the point. It never blindly trusts the
   model's own "SAFE". Every proposed command is checked, in code, against a narrow
   allow-list of reversible actions. Anything off the list is escalated, no matter how
   confident the AI sounds.
4. **Acts or escalates** — SAFE + allow-listed → apply the fix, verify recovery.
   Otherwise → page a human and touch nothing.

```
SAFE  + on the allow-list  ->  apply, verify the pod recovers
RISKY (or off the list)    ->  page a human, change nothing
```

## What it handled, live

| Incident | Root cause the AI found | Action | Result |
|----------|-------------------------|--------|--------|
| CrashLoopBackOff | a dropped `DB_HOST` env var | `set env` | auto-healed |
| ImagePullBackOff | image tag typo `nginx:alpin` | `set image` | auto-healed |
| Pending | requests `900Gi` memory (typo for `900Mi`) | `set resources` | auto-healed |
| OOMKilled | memory limit 4x too small | `set resources` | auto-healed |
| CrashLoopBackOff | **half-applied DB migration, inconsistent schema** | — | **escalated to a human** |

The fifth is the whole point: it looks identical to the first, but the log shows a
failed migration and persistent-state corruption. The AI recognised that no reversible
config fix repairs a broken schema — and that restarting or rolling back could make it
worse — so it paged a human and changed nothing.

## The guard-rail (the honest part)

`nightshift_agent.py` treats the AI's classification as a *recommendation only*. The
real gate is `command_is_allowed()`:

- **Allow-list** (reversible): `set env`, `set image`, `set resources`,
  `rollout restart`, `delete pod`, `scale` (bounded 1–10).
- **Hard blocks** (escalate no matter what): anything touching `pvc`,
  `persistentvolume`, `secret`, `namespace`, `delete deployment/svc/node`, `--all`,
  `--force`, `rollout undo`, `replicas=0`, `drain`, `cordon`.

Note the thin line: `set image` corrects a typo'd tag **and** is how you'd wrongly roll
back a broken deploy. The allow-list can't tell those apart by shape — it leans on the
AI defaulting to RISKY when unsure. So: a narrow hard boundary of what it *can* touch,
and everything past it is a page, not an action.

## Files

| File | What it is |
|------|-----------|
| `nightshift_agent.py` | the agent: poll Alertmanager → gather context → Claude → guard-rail → act/escalate |
| `scenarios/*.yaml` | the 5 broken workloads (4 safe, 1 risky), as real Deployments |
| `values-min.yaml` | trimmed kube-prometheus-stack values (Prometheus + Alertmanager, no Grafana) |
| `fast-rules.yaml` | a PrometheusRule with fast (`for: 1m`) pod alerts, so the demo doesn't wait 15 min |

## Run it yourself

```bash
# 1) a local cluster (kind on Docker or Podman), $0
kind create cluster --name nightshift

# 2) real Prometheus + Alertmanager
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install kps prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace -f values-min.yaml --wait
kubectl apply -f fast-rules.yaml

# 3) break things
kubectl apply -f scenarios/

# 4) let the AI take the pager (port-forward Alertmanager first)
kubectl -n monitoring port-forward svc/kps-kube-prometheus-stack-alertmanager 9093:9093 &
python nightshift_agent.py --watch      # or --once for a single sweep
```

You'll watch it heal four incidents and page a human for the fifth.

## Run it IN the cluster (hybrid, $0) — `deploy/`

The laptop version above runs the agent as a host process. You can also run it as a
real **in-cluster workload**, which is how you'd actually deploy auto-remediation —
and it makes the guard-rail stronger.

```
Pod: nightshift-agent  (ServiceAccount: nightshift-agent, RBAC-scoped)
  ├─ kubectl via the pod's ServiceAccount token   (no kubeconfig, no --context)
  ├─ polls Alertmanager over the cluster network  (no port-forward)
  └─ POST prompt ─► Service llm-bridge ─► host llm_bridge.py ─► claude ($0)
```

**RBAC becomes a second, harder guard-rail.** The code allow-list decides what the
agent *tries*; RBAC (`deploy/10-rbac.yaml`) decides what the API server will *let it
do*. The ServiceAccount can `patch` Deployments and `delete` Pods — and nothing else.
It has **no permission** to touch secrets, PVCs, namespaces, or delete a Deployment,
so even a bypassed code guard-rail can't become a catastrophe:

```
$ kubectl auth can-i patch deployments   --as=system:serviceaccount:default:nightshift-agent   # yes
$ kubectl auth can-i delete pvc          --as=system:serviceaccount:default:nightshift-agent   # no
$ kubectl auth can-i get secrets         --as=system:serviceaccount:default:nightshift-agent   # no
$ kubectl auth can-i delete deployments  --as=system:serviceaccount:default:nightshift-agent   # no
```

**The one piece that isn't in-cluster is the AI brain.** The agent runs `Claude` on a
personal subscription (the reason it's $0), and that CLI lives on your machine — a pod
can't use it. So `deploy/llm_bridge.py` runs on the host and the pod reaches it through
a `Service` + `Endpoints` (`deploy/00-llm-bridge-service.yaml`) pointing at the host.
**On a real cluster you'd delete the bridge and give the pod an Anthropic API key in a
Secret instead** (self-contained, but it bills per token).

```bash
# 0) host: run the LLM bridge (wraps your claude CLI)
python deploy/llm_bridge.py     # listens on :8799

# 1) point the bridge Service at your host IP, then apply it
#    (Endpoints IP = the host's address reachable from the cluster;
#     on Docker Desktop / WSL that's your host's LAN/WSL IP)
kubectl apply -f deploy/00-llm-bridge-service.yaml

# 2) build the agent image and load it into kind
docker build -t nightshift-agent:demo -f deploy/Dockerfile .   # or: podman build ...
kind load docker-image nightshift-agent:demo --name nightshift

# 3) deploy the agent + its RBAC, then watch it work
kubectl apply -f deploy/10-rbac.yaml -f deploy/20-deployment.yaml
kubectl apply -f scenarios/                     # break things
kubectl logs -f deploy/nightshift-agent         # watch it heal 4, escalate 1
```

`deploy/` contents: `llm_bridge.py` (host bridge), `00-llm-bridge-service.yaml`,
`10-rbac.yaml` (ServiceAccount + least-privilege Role), `20-deployment.yaml`,
`Dockerfile`, `entrypoint.sh` (builds an in-cluster kubeconfig from the SA token).

---
Part of the **DevOps Autopilot** series: an AI takes a real engineering seat — reviewer,
test-writer, attacker, and now on-call — and we stay honest about what it gets right and
where it's confidently wrong.
