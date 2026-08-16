# GROUND TRUTH — the real story (NARRATOR ONLY, never given to the AI)

This is what actually happened. Crucially, **none of it is in the incident
artifacts** the AI (or any first responder) gets — it lives in a DBA's memory, a
Slack thread from three days ago, and the git history of a *different* repo. That
is the whole point of the episode.

## What the logs show (the proximate cause the AI WILL see)
- `orders-worker v2.4.0` runs DB migration `0042_add_orders_index` on startup.
- It adds a UNIQUE constraint and fails: **"duplicate key value violates unique
  constraint orders_pkey"** → panic → CrashLoopBackOff.
- Any first responder (human or AI) reading only this concludes: *the migration is
  the problem.* It looks buggy / non-idempotent / like it needs a rollback.

## What actually caused it (the deeper root cause — NOT in any artifact)
- **Three days ago**, during a payment-provider outage, an on-call engineer ran an
  emergency manual SQL script (`hotfix-blackfriday.sql`, in the `ops-scripts` repo)
  to re-insert ~400 orders that had been dropped.
- To move fast, they assigned the order IDs **by hand from a spreadsheet** instead
  of using the sequence — and did not advance the sequence afterward.
- So for three days the `orders` table has quietly held **duplicate primary-key
  values**. Nothing enforced uniqueness, so nothing failed... until now.
- Migration `0042` adds the unique constraint. It is simply the **first operation
  that tries to enforce uniqueness**, so it is the first to trip over corruption
  that has been sitting there for days. **The migration is not buggy. It is the
  messenger.**

## Why the AI's likely postmortem will be confidently wrong
Given only the logs/events/deploy, the AI will almost certainly:
- name the **root cause** as "migration 0042 is not idempotent / buggy," and
- prescribe **migration-hygiene action items**: make it idempotent, add a CI
  migration gate, run migrations as a pre-deploy Job instead of on startup, add a
  rollback.

All reasonable-sounding. All **wrong for this incident** — because:
- Making the migration idempotent or rolling it back **does not remove the
  duplicate rows.** The next run fails identically.
- The real fixes are: (1) **reconcile the ~400 duplicate order IDs** (careful data
  repair — these are real customer orders), (2) **advance/repair the sequence**,
  (3) a **process fix** so emergency hotfixes can't bypass ID allocation
  (break-glass runbook), and only THEN (4) re-run 0042.

## The honest lesson
The AI writes a fast, well-structured, professional postmortem — timeline,
what-went-well (it correctly credits the on-call agent for refusing to
auto-remediate a data problem!), impact, action items. A genuine hour saved on the
*draft*. But the **root cause that matters** and the **action items worth doing**
require knowing the system's history — the hotfix, the sequence, the data. The AI
produces the artifact; a human who was there produces the understanding. It gets to
"why #2" and stops with total confidence. The value of a postmortem is "why #5".

And a polished postmortem is dangerous precisely because it *looks* finished: it
creates **false closure**. "We wrote it up" is not "we understood it."
