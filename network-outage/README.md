# AI debugs a Kubernetes network outage — the map was green, the territory was on fire

Companion to the DevOps Autopilot episode **"I Let an AI Debug a Kubernetes Network
Outage. It Blamed the Wrong Thing."** We gave an AI the tools a real on-call agent
gets — `kubectl` (read) and `kubectl exec` into pods — and let it run an outage. What
it does **not** have is a shell on the nodes. That boundary is the whole episode.

## The scenario

- [`scenarios/backend.yaml`](scenarios/backend.yaml) — an nginx serving a tiny response
  at `/` and a **1 MB** file at `/large.bin`. The size split is what makes the fault
  legible: small requests sail through; large responses hit it.
- [`scenarios/service-broken.yaml`](scenarios/service-broken.yaml) — **Act 1** fault: the
  Service selector is `app: web`, the pods are `app: web-backend`. Zero endpoints, total
  outage. Fixed by [`scenarios/service-fixed.yaml`](scenarios/service-fixed.yaml).
- [`scenarios/client.yaml`](scenarios/client.yaml) — a `netshoot` pod to curl from.
- [`inject-fault.sh`](inject-fault.sh) — **Act 2** fault, run **inside the node**: an
  `iptables` rule that drops every TCP packet from port 80 over ~1400 bytes. It lives on
  the node, in no Kubernetes object — `kubectl` cannot see it.

## What the AI did ([`ai_netdebug.py`](ai_netdebug.py))

**Act 1 — total outage.** It nailed it in seconds ([`netdebug/round1.txt`](netdebug/round1.txt)):
selector/label mismatch → zero endpoints, one-line `kubectl patch` to fix, plus an expert
warning not to put the `pod-template-hash` in the selector. The AI at its best.

**Act 2 — small works, large hangs.** The hard one ([`netdebug/round2.txt`](netdebug/round2.txt)).
Everything `kubectl` shows is green ([`capture/act2.txt`](capture/act2.txt)): endpoints
healthy, pods ready, no NetworkPolicy, and from inside a backend pod the app serves the
full 1 MB fine — nginx even logs `200 1048576`. The server swears it sent a megabyte; the
client got nothing.

The AI **reasons it out beautifully** and localizes it correctly: *"this is below
Kubernetes, in the data plane. The objects are healthy because they ARE healthy... the
kernel retransmits into the void."* Then, **85% confident**, it names the culprit: **an
MTU black-hole** — the most famous cause of this exact fingerprint. It even proposes a
size-threshold test ([`capture/threshold.txt`](capture/threshold.txt)), which shows a
clean cutoff around 1400 bytes: size-dependent, data-plane. All correct. And it's honest:
*"what I can't do from here: tcpdump on the node... I don't have it."*

## Where it's wrong (the point)

Drop below the line, onto the node ([`capture/reveal.txt`](capture/reveal.txt)):

```
# iptables -L FORWARD -n -v
 pkts bytes target  ...  match
   21  689K DROP    tcp  tcp spt:80 length 1400:65535   <-- a rule in nobody's YAML
```

Curl the large file and the DROP counter jumps (21 → 29). **It is not MTU.** It's a
stray firewall rule on the node. An MTU black-hole and a length-based DROP rule produce
the **identical** fingerprint — small works, large hangs, `200` then nothing — so from
everything the AI could see, they're the same. It picked the famous one, and its fix
(retune the CNI MTU) would have changed nothing, because there was never an MTU problem.

## The lesson

`kubectl` shows you the **control plane** — the desired state, the objects, the config.
This bug lived in the **data plane** — the actual packets on the actual node, in a rule
Kubernetes never knew about. The AI's map was complete and green, edge to edge. The
territory was on fire.

It reasons brilliantly right up to the boundary of what it can observe, then fills the
gap below that line with the most probable textbook cause — usually right, and here,
expensively, wrong.

> The rule: let the AI run the incident — it's fast, systematic, it narrowed a nasty
> data-plane bug to the right layer, and it was honest that node capture was the real
> confirmation it didn't have. But when it hands you a confident cause for something it
> just told you it can't fully see, treat it as the most *probable guess*, not the
> verdict — especially when the fix is expensive. **The AI narrows it down. You go below
> the line.**

## Run it yourself

```bash
kubectl apply -f scenarios/backend.yaml -f scenarios/client.yaml -f scenarios/service-broken.yaml
kubectl get endpoints web           # Act 1: <none>
kubectl apply -f scenarios/service-fixed.yaml   # endpoints populate, small works
# then, inside the node (docker/podman exec into it):
bash inject-fault.sh                # Act 2: large responses start hanging
python ai_netdebug.py               # -> netdebug/round1.txt, round2.txt
```

*Safe, local, $0 (kind). This is about the reasoning, not a drop-in tool.*
