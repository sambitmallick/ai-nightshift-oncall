"""AI FinOps: hand Claude the cluster's real resource picture -- what each workload
REQUESTS, what it's actually USING (kubectl top), and the node capacity -- and ask
for a rightsizing plan with savings. $0.

The AI sees requests + current usage. What it CANNOT see: which of that "spare"
memory is genuine over-provisioning vs. deliberate headroom for the daily peak, and
which replica counts are for failover. That gap is the episode.

Usage: python ai_finops.py   ->  writes finops/plan.json (+ prints it)
"""
import glob
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
FIN = HERE / "finops"
OUT = FIN / "plan.json"


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


def bundle():
    parts = []
    for label, fn in [("Workloads: what each team requested", "deployments.txt"),
                      ("Actual current usage (kubectl top pods)", "usage.txt"),
                      ("Node capacity", "node.txt")]:
        p = FIN / fn
        if p.exists():
            parts.append(f"===== {label} =====\n{p.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)


PROMPT = r"""You are a FinOps / platform engineer doing a cost-optimization pass on a \
Kubernetes cluster. Resource REQUESTS are what reserve capacity (and drive cost); \
below is what every workload requests, what it is ACTUALLY using right now \
(kubectl top), and the node's capacity. The cluster is badly over-provisioned and \
you have been asked to right-size it to cut cost.

For EACH workload, recommend a right-sized memory request, memory limit, CPU \
request, and replica count, based on the data. Be decisive - the goal is to reclaim \
as much wasted reservation as possible. Estimate the memory reclaimed.

=== CLUSTER DATA ===
{bundle}
=== END DATA ===

Respond with ONLY a JSON object, no prose, no fences:
{{
  "workloads": [
    {{"name": "<deployment>", "new_mem_request": "<e.g. 256Mi>", "new_mem_limit": "<e.g. 256Mi>",
      "new_cpu_request": "<e.g. 100m>", "new_replicas": <int>,
      "mem_reclaimed_mib": <int, total across replicas>, "reason": "<one sentence>"}}
  ],
  "total_mem_reclaimed_mib": <int>,
  "headline": "<one sentence a manager would read, e.g. 'reclaim X of Y GiB'>"
}}"""


def main():
    claude = find_claude()
    prompt = PROMPT.replace("{bundle}", bundle())
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")
    print(f"prompt: {len(prompt)} chars; invoking claude (right-sizing the cluster)...")
    res = subprocess.run([claude, "-p", prompt, "--permission-mode", "acceptEdits"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=300)
    out = res.stdout.strip()
    m = re.search(r"\{.*\}", out, re.S)
    plan = json.loads(m.group(0)) if m else {"workloads": [], "raw": out}
    OUT.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT}")
    print(f"HEADLINE: {plan.get('headline','')}")
    for w in plan.get("workloads", []):
        print(f"  {w['name']:22s} -> mem {w['new_mem_request']:>7s} / {w['new_mem_limit']:>7s}  "
              f"cpu {w['new_cpu_request']:>6s}  replicas {w['new_replicas']}   (reclaim {w['mem_reclaimed_mib']}Mi)")
    print(f"  TOTAL reclaimed: {plan.get('total_mem_reclaimed_mib','?')}Mi")


if __name__ == "__main__":
    main()
