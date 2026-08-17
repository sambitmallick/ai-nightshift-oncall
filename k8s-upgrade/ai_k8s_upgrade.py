"""AI resource migration: hand Claude the legacy manifests (APIs removed before 1.33)
and ask it to migrate them to current stable APIs so they apply on the upgraded cluster.
Then we apply its output and test whether behavior was preserved.

Usage: python ai_k8s_upgrade.py
  -> migrated/migrated.yaml   (the AI's migrated manifests)
  -> migrated/reasoning.txt   (its explanation, incl. the Ingress pathType judgment call)
"""
import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent
SRC = HERE / "scenarios" / "legacy-manifests.yaml"
OUT = HERE / "migrated"
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


MIGRATE = """You are upgrading a Kubernetes cluster to v1.33. The manifests below use API \
versions that were REMOVED before v1.33, so they no longer apply. Migrate every resource \
to its current stable API version so it applies cleanly on v1.33, preserving the original \
intent. Output ONLY the migrated YAML manifests, --- separated. No prose, no code fences.

=== MANIFESTS ===
{m}
=== END ==="""

EXPLAIN = """You just migrated these legacy Kubernetes manifests to v1.33 APIs:

{m}

For the Ingress specifically: networking.k8s.io/v1 requires a `pathType` on every path, \
but the old extensions/v1beta1 Ingress had no such field. Which pathType did you choose \
for the "/" and "/api" paths, and why? Be brief and honest about the fact that the old \
manifest did not actually specify the matching behavior."""


def ask(claude, prompt):
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")
    return subprocess.run([claude, "-p", prompt, "--permission-mode", "acceptEdits"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env=env, timeout=300).stdout.strip()


def main():
    claude = find_claude()
    manifests = SRC.read_text(encoding="utf-8")
    print("migrating legacy manifests to v1.33 APIs...")
    out = ask(claude, MIGRATE.replace("{m}", manifests))
    out = re.sub(r"^```[a-zA-Z]*\n?|```$", "", out.strip(), flags=re.M).strip()
    (OUT / "migrated.yaml").write_text(out, encoding="utf-8")
    print("wrote migrated/migrated.yaml\n")
    # what pathType did it pick?
    pt = re.findall(r"pathType:\s*(\w+)", out)
    print("Ingress pathType choices:", pt or "(none found)")
    print("\ncapturing the AI's reasoning on the pathType judgment call...")
    r = ask(claude, EXPLAIN.replace("{m}", out))
    (OUT / "reasoning.txt").write_text(r, encoding="utf-8")
    print(r)


if __name__ == "__main__":
    main()
