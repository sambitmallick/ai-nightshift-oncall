# AI incident postmortem — an excellent write-up with the wrong root cause

Companion to the DevOps Autopilot episode **"I Let an AI Write My Incident
Postmortem. It Nailed Everything but the Root Cause."** This is the closing beat of
the on-call trilogy: the [night-shift agent](../) *escalated* a crash-looping
service (a half-applied DB migration) instead of auto-fixing it. That was the right
call — but someone still has to write the postmortem. So we asked the AI to.

## The setup

The AI was given **exactly what a first responder gets, and nothing more** — the
files in [`incident/`](incident/): the alert, the Kubernetes event timeline, the
pod's `describe`, the container logs, the Deployment that shipped, and the on-call
agent's own escalation. [`ai_postmortem.py`](ai_postmortem.py) hands all of it to
Claude ($0) and asks for a full blameless postmortem.

## What it produced

[`POSTMORTEM-the-AI-wrote.md`](POSTMORTEM-the-AI-wrote.md) — and it's genuinely good:
a crisp summary, a real impact assessment, a timestamped timeline reconstructed from
the events, a proper 5-Whys, it credits the on-call agent for the right call, and a
table of prioritized action items with owners. Better than most humans write, in ~30
seconds.

## Where it's wrong (the point)

Read the **root cause**. The AI says: *"a non-atomic, non-idempotent migration, run
inline on startup, never validated against real data."* Every word is true. And it's
wrong — see [`the-real-root-cause.md`](the-real-root-cause.md).

The duplicate keys the migration tripped over were **already in the data**, put there
three days earlier by an emergency hotfix that hand-assigned order IDs and never
advanced the sequence. **The migration didn't create the mess — it's the first thing
to enforce uniqueness, so it's the first to trip over it. It's the messenger.**

So the AI's action items ("make the migration idempotent", "add a CI migration gate")
are all reasonable hygiene — and **not one of them fixes this incident**. Make the
migration idempotent and it fails identically on the next run, because the corrupt
data is still there. The action item that actually matters — reconcile the duplicate
orders, repair the sequence, fix the break-glass process — is nowhere on the list,
because it lives in a DBA's memory and a Slack thread, not in any log.

## The lesson

The AI produced the **artifact** — structure, timeline, writing, the obvious fixes —
flawlessly. What it cannot produce is the **understanding**: the true root cause and
the fix that matters needed the system's history, and the AI has exactly zero of your
org's memory. It writes the story the logs tell, with total confidence, whether or
not that story is the real one.

And a polished, confident postmortem with the wrong root cause is *more* dangerous
than a bad one — it gets filed, shared, and believed. It looks finished. **"We wrote
it up" is not "we understood it."**

> The rule: let the AI write the postmortem — it's a great first draft. But treat the
> root cause as a *claim*, not a conclusion, until a human who knows the system has
> checked it against everything that isn't in the logs. The AI drafts. You decide.

*(`the-real-root-cause.md` is the intended backstory for this teaching scenario — the
context that, by design, is absent from the incident artifacts. That absence is the
whole exercise.)*
