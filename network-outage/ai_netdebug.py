"""AI network on-call: hand Claude exactly what a first responder gathers for a
Kubernetes networking outage, and let it diagnose. Two rounds:

  Round 1 (Act 1): the Service is up but users get nothing. -> the AI should nail
                   the classic "no endpoints / selector mismatch" instantly.
  Round 2 (Act 2): selector fixed, small requests work, but large responses hang -
                   and EVERYTHING kubectl can show is green. This is the crux: the
                   cause is in the node's packet path, below the AI's kubectl reach.

Its realistic tools: kubectl (read), and kubectl exec INTO pods. NOT a shell on the
node. That boundary is the whole episode.

Usage: python ai_netdebug.py   ->  netdebug/round1.txt, netdebug/round2.txt
"""
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:  # keep Windows consoles from crashing on unicode (minus signs, arrows, ...)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent
CAP = HERE / "capture"
OUT = HERE / "netdebug"
OUT.mkdir(exist_ok=True)


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


TOOLS = ("You are the on-call SRE for a Kubernetes cluster. Your tools: kubectl (read-only) "
         "and `kubectl exec` INTO pods. You do NOT have a shell on the nodes. Be concise and decisive.")

R1 = TOOLS + """

INCIDENT: users report "the web service is completely down - nothing loads."
Here is what I've gathered so far:

{ev}

1) What is the root cause, in one sentence?
2) The exact kubectl command to FIX it.
Keep it to a few lines."""

R2 = TOOLS + """

FOLLOW-UP: I fixed the selector and the Service has endpoints again. Small requests now
work. But users still report failures on anything large - downloads, uploads, big API
responses just hang. Here is everything I can see:

{ev}

Note: from INSIDE a backend pod, curling localhost/large.bin returns the full 1MB fine,
and nginx's own access log shows it served "200 1048576" for the request that the client
saw hang. Every Kubernetes object is healthy.

1) What is your leading root-cause hypothesis, and how confident are you?
2) What is the single next command you'd run to confirm it - and can you actually run
   that with kubectl / kubectl-exec, or would it need something you don't have?
3) What's the fix?
Be honest about what you can and cannot see from here."""


def ask(claude, prompt):
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")
    res = subprocess.run([claude, "-p", prompt, "--permission-mode", "acceptEdits"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env=env, timeout=300)
    return res.stdout.strip()


def main():
    claude = find_claude()
    ev1 = (CAP / "act1.txt").read_text(encoding="utf-8").strip()
    ev2 = (CAP / "act2.txt").read_text(encoding="utf-8").strip()

    print("=== ROUND 1 (Act 1: total outage) ===")
    r1 = ask(claude, R1.replace("{ev}", ev1))
    (OUT / "round1.txt").write_text(r1, encoding="utf-8")
    print(r1)

    print("\n\n=== ROUND 2 (Act 2: small works, large hangs, kubectl all green) ===")
    r2 = ask(claude, R2.replace("{ev}", ev2))
    (OUT / "round2.txt").write_text(r2, encoding="utf-8")
    print(r2)


if __name__ == "__main__":
    main()
