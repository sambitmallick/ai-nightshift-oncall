# AI FinOps — an 82% cost cut that took down checkout at peak

Companion to the DevOps Autopilot episode **"I Let an AI Cut My Cloud Bill by 82%.
Then It Took Down Checkout."** This is the other side of the on-call coin: the
[night-shift agent](../) spends its nights *healing* OOMKills and crashloops. Here an
AI *creates* one — by doing exactly what a FinOps cost-optimizer is supposed to do.

## The setup

Four workloads on a `kind` cluster ([`scenarios/finops-workloads.yaml`](scenarios/finops-workloads.yaml)),
each reserving far more memory than it uses at off-peak:

| workload | reserved | actually using (off-peak) |
|---|---|---|
| checkout-service | 512Mi × 3 replicas | 160Mi, 0m CPU |
| analytics-worker | 2Gi × 1 | 185Mi, 0m CPU |
| image-thumbnailer | 1Gi × 2 | 120Mi, 0m CPU |
| notification-sender | 768Mi × 1 | 90Mi, 0m CPU |

About **6.25 GiB reserved, under 1 GiB used.** The captured picture is in
[`captured/`](captured/) — `deployments.txt`, `usage.txt` (`kubectl top`), `node.txt`.

## What the AI did

[`ai_finops.py`](ai_finops.py) hands Claude the requests, the live usage, and the node
capacity ($0, no API key) and asks for a right-sizing plan. It produced
[`captured/plan.json`](captured/plan.json):

> **"Right-sizing reclaims 5.1 of 6.25 GiB reserved memory (82%), cutting the
> cluster's reservation from 6400Mi to 1152Mi, with headroom intact."**

And **three of the four calls are exactly right.** Analytics 2Gi→256Mi,
image-thumbnailer two replicas→one, notification-sender 768Mi→128Mi. Those services
were genuinely, wildly over-provisioned. Finding that by hand is tedious; the AI did
it in thirty seconds. That's real value.

## Where it's wrong (the point)

Read the checkout line in `plan.json`. Off-peak, checkout used 160Mi of its 512, so
the AI cut the request to 256Mi, **capped the limit at 384Mi, and dropped 3 replicas
to 2** — with this reasoning, verbatim:

> *"Each pod uses only 160Mi of its 512Mi request and 0m CPU; 256Mi gives ~60%
> headroom and 2 replicas still provide HA for the user-facing path."*

Confident, reasonable, and wrong. That 512Mi was never waste — it was the **peak**.
Checkout at 9am is not checkout at 8pm. When the evening rush fills the cart cache, the
working set climbs toward ~450Mi, straight through the AI's 384Mi ceiling.
[`scenarios/checkout-peak.yaml`](scenarios/checkout-peak.yaml) reproduces that peak, and
[`captured/peak.txt`](captured/peak.txt) is the result:

```
checkout-service-5f7ccdf87d-gvmrb   0/1   OOMKilled   4   ...
checkout-service-5f7ccdf87d-lbkwv   0/1   OOMKilled   4   ...
```

Both replicas OOMKilled into CrashLoopBackOff — both, because the AI also cut the
third replica that was the failover. **Payments down, at peak.** Meanwhile the three
correctly-cut services keep running happily ([`captured/peak-all.txt`](captured/peak-all.txt)) —
the cut isn't reckless everywhere, just in the one place it couldn't see.

## The lesson

A metrics snapshot tells you what a workload is **using**. It does not tell you what
that workload is **for**. Headroom for the peak and a failover replica look identical
to waste in a spec at off-peak. The AI optimized the one number it could see — spend,
because spend is data — and was blind to the one that mattered: the cost of downtime,
which lives in no `kubectl top`.

Cost is easy to measure. Reliability is invisible until it's gone. **The "waste" you
cut is the reliability you were paying for.** And notice the loop: the OOM the AI
*created* here is the exact kind the [night-shift agent](../) exists to *heal*. FinOps
and on-call are the same budget, seen from two sides.

> The rule: let the AI find the candidates — that 82% list is real. But right-sizing
> is a risk decision, not a math problem. Before you cut headroom or a replica —
> especially on the tier that touches money — a human who knows the traffic shape
> signs off. **The AI proposes the cut. You own the outage.**

## Run it yourself

```bash
# needs a kind cluster with metrics-server (see ../ for the $0 setup)
kubectl apply -f scenarios/finops-workloads.yaml
kubectl top pods                      # the reservation vs. the reality
python ai_finops.py                   # -> captured/plan.json (the 82% plan)
# apply the plan's numbers, then:
kubectl apply -f scenarios/checkout-peak.yaml   # the evening peak -> OOMKilled
```

*Safe, local, $0. This is about the reasoning, not a drop-in tool.*
