"""AI incident postmortem: hand Claude exactly what a first responder has -- the
alert, the pod timeline, the describe, the logs, the deployment, and the on-call
agent's own actions -- and ask for a complete, blameless postmortem. $0.

The AI is deliberately NOT given the ground truth (incident/GROUND-TRUTH.md): the
3-day-old hotfix that actually corrupted the data. That backstory lives in a DBA's
memory, not in these artifacts -- which is the whole point.

Usage: python ai_postmortem.py
"""
import glob
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
INC = HERE / "incident"
OUT = HERE / "postmortem.md"

ARTIFACTS = [
    ("The alert that fired (Alertmanager)", "06-alert.txt"),
    ("Pod status", "01-pod-status.txt"),
    ("Timeline of Kubernetes events", "02-events.txt"),
    ("kubectl describe pod", "03-describe.txt"),
    ("Container logs", "04-logs.txt"),
    ("The Deployment that shipped (v2.4.0)", "05-deployment.yaml"),
    ("The on-call AI agent's action + reasoning", "07-agent-escalation.txt"),
]


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
    for label, fn in ARTIFACTS:
        p = INC / fn
        if p.exists():
            txt = p.read_text(encoding="utf-8", errors="replace").strip()
            parts.append(f"===== {label}  ({fn}) =====\n{txt[:3500]}")
    return "\n\n".join(parts)


PROMPT = r"""You are a senior SRE writing the incident postmortem for a production \
outage. Below is EVERYTHING available about the incident: the alert, the pod status \
and Kubernetes event timeline, the describe output, the container logs, the \
Deployment that shipped, and the actions the automated on-call agent took. The \
service is `orders-worker` (order processing).

Write a complete, professional, BLAMELESS postmortem in Markdown with these \
sections:
- **Summary** (2-3 sentences: what happened, impact)
- **Impact** (what was degraded, for whom, how long -- infer from the evidence)
- **Detection & Timeline** (a timestamped table, from the real events)
- **Root Cause Analysis** -- do an explicit 5 Whys, ending at the deepest cause you \
can justify FROM THE EVIDENCE. State the root cause clearly.
- **What went well**
- **What went poorly**
- **Action Items** -- specific, prioritized (P0/P1/P2), each with an owner role and \
what it prevents.

Ground every claim in the evidence below. Be specific (cite log lines, event \
timestamps, spec fields). This will be read by the whole engineering org.

=== INCIDENT ARTIFACTS ===
{bundle}
=== END ARTIFACTS ===

Output ONLY the Markdown postmortem, no preamble, no fences."""


def main():
    claude = find_claude()
    prompt = PROMPT.replace("{bundle}", bundle())
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")
    print(f"prompt: {len(prompt)} chars; invoking claude (writing the postmortem)...")
    res = subprocess.run([claude, "-p", prompt, "--permission-mode", "acceptEdits"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=400)
    out = res.stdout.strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1].rsplit("```", 1)[0]
    OUT.write_text(out.strip() + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(out.splitlines())} lines)")
    if res.stderr.strip():
        print("stderr:", res.stderr.strip()[:200])


if __name__ == "__main__":
    main()
