# AI FinOps, round 2 — a month of metrics, and the cut it still got wrong

Companion to the DevOps Autopilot episode **"I Gave an AI a Month of Metrics. It
Still Cut the Wrong Thing."** This is the sequel to [`finops/`](../finops/), where an
AI right-sized a cluster off a single `kubectl top` reading, missed the evening peak,
and OOM-killed checkout. The fair objection to that episode was unanimous: *nobody
resizes off one snapshot — use the real history.* Correct. So this time we gave it
exactly that.

## The setup

[`gen_history.py`](gen_history.py) produces **30 days** of `checkout-service` memory
in the shape a Prometheus export would have ([`history/checkout_30d.csv`](history/checkout_30d.csv)):
a clean daily rhythm — quiet overnight (~150Mi), a consistent evening peak (~450Mi as
the cart cache fills), Fridays a little hotter. Two replicas, healthy the whole month.
The single worst moment all month: **494Mi**. The summary we hand the AI is
[`history/summary.txt`](history/summary.txt).

## What the AI did — carefully

[`ai_finops_history.py`](ai_finops_history.py) hands Claude the month of data ($0) and
asks for a right-sizing plan ([`history/plan.json`](history/plan.json)). And it does a
**genuinely careful, defensible job**:

- **Keeps both replicas** — it explicitly reasons that dropping to one *"would remove
  failover headroom"* for a payment path. Good instinct.
- **Trims the memory limit 768Mi → 640Mi**, justified as *"30% headroom over the worst
  peak observed all month."* Textbook FinOps.

## The win (be fair — it got better)

Apply the plan and test it against the worst evening the data ever showed
([`scenarios/checkout-win.yaml`](scenarios/checkout-win.yaml), ~495Mi):
[`history/win.txt`](history/win.txt) —

```
checkout-svc-79dc-52gpk  1/1  Running  0   ...  496Mi  (of 640 limit)
checkout-svc-79dc-bbvff  1/1  Running  0   ...  496Mi  (of 640 limit)
```

Zero restarts. With a month of data instead of a snapshot, the AI sized this
**correctly**. Every peak the history contained, it handles.

## The failure (the point)

Then **launch day** — traffic that is nowhere in the 30 days
([`scenarios/checkout-launch.yaml`](scenarios/checkout-launch.yaml), ~720Mi):
[`history/launch.txt`](history/launch.txt) —

```
checkout-svc-795c-r2xkq  0/1  OOMKilled  ...
checkout-svc-795c-zkjp6  0/1  OOMKilled  ...
```

Both replicas OOMKilled. The 640Mi ceiling — 30% over the worst day on record — is not
enough for an event categorically bigger than any day in the data.

## The lesson

A month of metrics is a **perfect record of the past**. But a service doesn't fall over
because of what happened — it falls over because of what *hasn't happened yet*.

- **The cut was the launch margin.** The 768Mi limit looked like 128Mi of pure waste in
  30 days of data. It wasn't waste — it was the margin someone left for the day traffic
  breaks the pattern. History gives you a confident number (the worst it's ever been),
  but that's a **floor, not a ceiling**; the biggest event is usually still ahead of you.
- **It kept the margin it could name, and cut the one it couldn't.** The AI protected
  the failover *replica* (its purpose has a name — "availability") but trimmed the limit
  *headroom* (just "unused bytes"). It's not that AI ignores safety margin — it keeps
  the margin whose purpose is written down where it can read it.

> The division of labor: history sizes the **floor** — the capacity you always need,
> the measurable part. Let the AI do that; it's faster and better than you. But the
> **ceiling** — room for the event that isn't in the data yet — is a business judgment
> about what this service is worth on its worst possible day. That lives in your
> business, not in Prometheus. **The AI sizes the floor. You set the ceiling.**

Two episodes, one lesson from both sides: a snapshot misses the peak it never saw; a
month nails every peak it saw and misses the one still coming. More data made it
*better*. It did not make it *enough*.

## Run it yourself

```bash
python gen_history.py               # -> history/ (30 days + summary)
python ai_finops_history.py         # -> history/plan.json (the careful plan)
kubectl apply -f scenarios/checkout-win.yaml     # worst observed evening -> holds
kubectl apply -f scenarios/checkout-launch.yaml  # launch day -> OOMKilled
```

*Safe, local, $0 (kind). This is about the reasoning, not a drop-in tool.*
