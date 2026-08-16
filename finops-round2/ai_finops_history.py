"""AI FinOps, round 2: this time we give the AI what everyone said we should -
a full MONTH of real memory history for checkout-service, not a single reading.
It sizes the memory correctly for every peak in the data. The point of the
episode is what that month of data still cannot show it.

Usage: python ai_finops_history.py   ->  writes history/plan.json (+ prints it)
"""
import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
HIST = HERE / "history"
OUT = HIST / "plan.json"


def find_claude():
    c = shutil.which("claude")
    if c:
        return c
    for p in [os.path.expanduser(r"~\.local\bin\claude.exe"), os.path.expanduser(r"~\.local\bin\claude")]:
        if os.path.exists(p):
            return p
    g = glob.glob(os.path.expanduser(r"~\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe"))
    if g:
        return sorted(g)[-1]
    raise RuntimeError("claude CLI not found")


PROMPT = r"""You are a FinOps / platform engineer doing a cost-optimization pass on a \
production service. Unlike a quick snapshot, you have a FULL MONTH of real memory \
history from Prometheus below - percentiles and the daily peak for all 30 days. \
Resource requests/limits are what reserve capacity and drive cost. checkout-service \
is over-provisioned; right-size its memory to cut cost, using the history to stay safe.

=== 30 DAYS OF PRODUCTION DATA ===
{bundle}
=== END DATA ===

Recommend a right-sized memory request and limit per replica, and comment on whether \
the replica count looks right given the data. Be decisive - reclaim what the month of \
data shows is genuinely spare, while keeping enough headroom to be safe for this traffic.

Respond with ONLY a JSON object, no prose, no fences:
{{
  "new_mem_request": "<e.g. 384Mi>",
  "new_mem_limit": "<e.g. 512Mi>",
  "mem_reclaimed_per_replica_mib": <int>,
  "replica_note": "<one sentence on the replica count>",
  "reasoning": "<2-3 sentences: how the 30-day data justifies this sizing>",
  "headline": "<one sentence a manager would read>"
}}"""


def main():
    claude = find_claude()
    summary = (HIST / "summary.txt").read_text(encoding="utf-8").strip()
    prompt = PROMPT.replace("{bundle}", summary)
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")
    print(f"prompt: {len(prompt)} chars; invoking claude (right-sizing from 30 days of data)...")
    res = subprocess.run([claude, "-p", prompt, "--permission-mode", "acceptEdits"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=300)
    out = res.stdout.strip()
    m = re.search(r"\{.*\}", out, re.S)
    plan = json.loads(m.group(0)) if m else {"raw": out}
    OUT.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")
    print(f"HEADLINE: {plan.get('headline','')}")
    print(f"  memory: request {plan.get('new_mem_request')} / limit {plan.get('new_mem_limit')}  "
          f"(reclaim {plan.get('mem_reclaimed_per_replica_mib')}Mi/replica)")
    print(f"  replicas: {plan.get('replica_note')}")
    print(f"  reasoning: {plan.get('reasoning')}")


if __name__ == "__main__":
    main()
