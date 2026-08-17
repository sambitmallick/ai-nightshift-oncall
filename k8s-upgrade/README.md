# AI runs a full Kubernetes upgrade — it aced it, then handed me the one decision it couldn't make

Companion to the DevOps Autopilot episode **"I Let an AI Run My Entire Kubernetes
Upgrade. Here's the One Decision It Couldn't Make."** We gave an AI the scariest
operation in Kubernetes — a live cluster upgrade, **1.32 → 1.33**, on a real two-node
`kind` cluster — and watched every command. It ran the whole thing cleanly. The
interesting part is the one place it stopped.

## The four layers of an upgrade

1. **Pre-flight** — `kubeadm upgrade plan` (reads the live cluster, prints what moves)
   + a deprecation scan with `pluto`, which flags three resources whose APIs the
   upgrade removes. To prove it, applying them on 1.33 is refused by the API server.
   → [`capture/preflight.txt`](capture/preflight.txt),
   [`capture/deprecation-scan.txt`](capture/deprecation-scan.txt)
2. **Control plane** — `kubeadm upgrade apply v1.33.1`: apiserver, scheduler,
   controller-manager, etcd rolled live, one at a time, the API never dropping.
   → [`capture/cp-upgrade.txt`](capture/cp-upgrade.txt)
3. **Nodes** — drain the worker **first**, upgrade the kubelet + `kubeadm upgrade node`,
   uncordon **last**. Both nodes end on 1.33.1.
   → [`capture/node-upgrade.txt`](capture/node-upgrade.txt)
4. **Resources** — migrate the flagged manifests to current APIs
   ([`ai_k8s_upgrade.py`](ai_k8s_upgrade.py)).

**Layers 1–3 were flawless** — real toil and real risk, handled calmly and in the
right order. This is the mechanical 90% of an upgrade, and the AI is genuinely good at
it.

## Layer 4: the migration — also excellent

The AI migrated all three legacy manifests
([`scenarios/legacy-manifests.yaml`](scenarios/legacy-manifests.yaml)) →
[`migrated/migrated.yaml`](migrated/migrated.yaml). Not a dumb find-and-replace:

- **HPA** `autoscaling/v2beta1` → `v2`, re-nesting the metric `target`.
- **Ingress** `extensions/v1beta1` → `networking.k8s.io/v1`, and it **promoted the
  deprecated `kubernetes.io/ingress.class` annotation to the proper `ingressClassName`
  field**.
- **PDB** `policy/v1beta1` → `v1`.

Applied clean. `curl` the app: `/`, `/api`, and the sub-path `/api/users` all route
correctly. It nailed this too.

## The one place it stopped (the point)

`networking.k8s.io/v1` requires a **`pathType`** on every path. `extensions/v1beta1`
**had no such field** — so the app's matching intent was never written down anywhere.
Read [`migrated/reasoning.txt`](migrated/reasoning.txt): the AI chose
`ImplementationSpecific` (the fidelity-preserving value `kubectl convert` itself uses),
and then said, in plain words, that the original manifest didn't specify this, that it
wouldn't encode an assumption the manifest never made, and that the explicit choice was
**mine to make**. It didn't guess. It handed the decision back.

And it was right to — because that decision is a live landmine
([`capture/ambiguity.txt`](capture/ambiguity.txt)). Make the other perfectly reasonable
choice, `pathType: Exact` ([`scenarios/ingress-exact.yaml`](scenarios/ingress-exact.yaml)):

```
curl .../api        -> API-BACKEND   (exact /api still works)
curl .../api/users  -> WEB-BACKEND   <- the WRONG service. silently. no error.
```

Same manifest shape, one field's value, and requests quietly go to the wrong place.

## The lesson

Every single thing the AI produced applied cleanly. But **applied is not correct**, and
the gap between them here was a single decision the manifest never contained: *what did
you mean this path to match?* That intent lived in a person's head, not the file — so
the AI couldn't read it, and neither could you. **Valid YAML is not preserved behavior.**

> Let the AI run the upgrade — genuinely. The pre-flight, the control plane, the node
> dance, the mechanical migration: faster, calmer, more disciplined than by hand. But
> every place it invents something the manifest never said, it borrowed a decision from
> you — whether it flags it or not. Your job on an AI-run upgrade isn't the typing
> anymore. It's finding the borrowed decisions, and owning them.

## Run it yourself (the real commands)

```bash
# a 2-node cluster on the OLD version
kind create cluster --name upgrade --image kindest/node:v1.32.5 --config scenarios/kind-upgrade.yaml
kubectl apply -f scenarios/apps.yaml   # + an ingress controller + the legacy manifests

# --- inside the control-plane node (docker/podman exec), as the real upgrade ---
#   (kind/podman needs: --ignore-preflight-errors=SystemVerification)
kubeadm upgrade plan
kubeadm upgrade apply v1.33.1 -y --ignore-preflight-errors=SystemVerification
# replace kubelet binary + systemctl restart kubelet
# --- worker ---
kubectl drain upgrade-worker --ignore-daemonsets --delete-emptydir-data
kubeadm upgrade node && (replace kubelet; systemctl restart kubelet)
kubectl uncordon upgrade-worker

# --- resources ---
pluto detect-files -d scenarios --target-versions k8s=v1.33.0   # what breaks
python ai_k8s_upgrade.py                                        # -> migrated/migrated.yaml
kubectl apply -f migrated/migrated.yaml
```

*Safe, local, $0 (kind). This is about the reasoning, not a drop-in tool.*
