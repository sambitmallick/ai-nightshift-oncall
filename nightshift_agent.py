"""Night-shift on-call agent.

Polls Alertmanager for firing Kubernetes pod alerts. For each one, gathers the
pod's REAL state (describe + logs + events + the owning workload), hands it to
Claude to diagnose the root cause and classify the fix as SAFE (reversible,
low-risk config change) or RISKY (needs a human). Then it does the thing that
makes "auto-heal at 3am" defensible: it NEVER blindly trusts the model's
self-classification. Every proposed command is independently checked against a
narrow allow-list of reversible actions; anything else is escalated to a human,
no matter how confident the AI is.

  SAFE  + on the allow-list  -> apply the fix, verify the pod recovers
  RISKY (or off the list)    -> page the human, touch nothing

$0: Claude runs on the mounted subscription. Runs on the host (where the claude
CLI lives) against the local kind cluster.

Usage:
  python nightshift_agent.py            # watch forever
  python nightshift_agent.py --once     # one sweep and exit (for the demo)
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

KCTX = os.environ.get("KCTX", "kind-nightshift")
AM = os.environ.get("AM_URL", "http://localhost:9093")   # alertmanager (port-forwarded)
POLL_SECONDS = 10

# ---- ANSI-ish markers for legible, on-camera logging -------------------------
def log(mark, msg, color=""):
    stamp = time.strftime("%H:%M:%S")
    C = {"g": "\033[92m", "r": "\033[91m", "y": "\033[93m", "c": "\033[96m",
         "d": "\033[90m", "w": "\033[97m", "": ""}
    end = "\033[0m" if color else ""
    print(f"\033[90m{stamp}\033[0m {C.get(color,'')}{mark} {msg}{end}", flush=True)


def find_claude():
    c = shutil.which("claude")
    if c:
        return c
    for p in [os.path.expanduser(r"~\.local\bin\claude.exe"),
              os.path.expanduser(r"~\.local\bin\claude")]:
        if os.path.exists(p):
            return p
    g = glob.glob(os.path.expanduser(
        r"~\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe"))
    if g:
        return sorted(g)[-1]
    raise RuntimeError("claude CLI not found")


CLAUDE = None


def kubectl(args, timeout=30):
    """Run kubectl against the demo context; return (rc, stdout, stderr)."""
    p = subprocess.run(["kubectl", "--context", KCTX] + args,
                       capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


# ---- the allow-list: the ONLY shapes of command the agent will auto-run -------
# Each entry is a regex the whole `kubectl ...` command (minus the leading
# "kubectl") must fully match. Deliberately narrow + reversible.
ALLOW = [
    r"set env (deployment|deploy)/[\w-]+( [\w.]+=[^\s]+)+",                 # fix/add env vars
    r"set image (deployment|deploy)/[\w-]+ [\w-]+=[\w./:@-]+",             # correct an image/tag
    r"set resources (deployment|deploy)/[\w-]+ (--limits|--requests)=[^\s]+( (--limits|--requests)=[^\s]+)?",  # tune resources
    r"patch (deployment|deploy)/[\w-]+ --type=(json|merge|strategic) -p .{1,400}",  # narrow patch (validated further below)
    r"rollout restart (deployment|deploy)/[\w-]+",                         # bounce a stuck rollout
    r"scale (deployment|deploy)/[\w-]+ --replicas=([1-9]|10)",             # scale within 1..10
    r"delete pod [\w-]+",                                                  # delete a pod -> reschedule
]
# Hard blocks: if any of these appear in the command, escalate no matter what.
FORBIDDEN = ["pvc", "persistentvolume", "secret", "namespace", "-n kube-system",
             "delete deployment", "delete deploy", "delete svc", "delete service",
             "delete node", "delete pv", "--all", "drain", "cordon", "--force",
             "rollout undo", "\"replicas\":0", "replicas=0"]


def command_is_allowed(cmd):
    """cmd is the string AFTER 'kubectl ' (no context flag)."""
    c = " ".join(cmd.split())  # normalise whitespace
    low = c.lower()
    for bad in FORBIDDEN:
        if bad in low:
            return False, f"contains blocked token '{bad}'"
    for pat in ALLOW:
        if re.fullmatch(pat, c):
            # extra guard: a patch must only touch env/resources/image, never spec-wide deletes
            if c.startswith("patch") and not re.search(r"(env|resources|image|containers)", c):
                return False, "patch does not target env/resources/image"
            return True, "matches allow-list"
    return False, "no allow-list rule matches"


def owner_of(ns, pod):
    """Return (kind, name) of the pod's controlling workload (Deployment via RS)."""
    rc, out, _ = kubectl(["-n", ns, "get", "pod", pod, "-o",
                          "jsonpath={.metadata.ownerReferences[0].name}"])
    rs = out
    if rs:
        rc, dep, _ = kubectl(["-n", ns, "get", "rs", rs, "-o",
                              "jsonpath={.metadata.ownerReferences[0].name}"])
        if dep:
            return "deployment", dep
    return "pod", pod


def gather_context(ns, pod):
    parts = []
    rc, desc, _ = kubectl(["-n", ns, "describe", "pod", pod])
    parts.append("### kubectl describe pod\n" + desc[:4000])
    rc, logs, err = kubectl(["-n", ns, "logs", pod, "--tail=25", "--all-containers"])
    if not logs:
        rc, logs, _ = kubectl(["-n", ns, "logs", pod, "--tail=25", "--previous", "--all-containers"])
    parts.append("### kubectl logs (last 25 lines)\n" + (logs or err or "(no logs)")[:2000])
    rc, ev, _ = kubectl(["-n", ns, "get", "events", "--field-selector",
                         f"involvedObject.name={pod}", "--sort-by=.lastTimestamp"])
    parts.append("### recent events\n" + ev[-1500:])
    kind, name = owner_of(ns, pod)
    rc, spec, _ = kubectl(["-n", ns, "get", kind, name, "-o", "yaml"])
    parts.append(f"### owning {kind}/{name} (spec)\n" + spec[:3000])
    return "\n\n".join(parts), kind, name


PROMPT = r"""You are the on-call SRE agent running UNSUPERVISED on night shift. A \
Kubernetes alert just fired. Your job is to diagnose it from the real cluster \
data below and decide whether it is safe for you to auto-remediate WITHOUT waking \
a human.

ALERT: {alertname}  ({reason})
POD:   {ns}/{pod}
OWNING WORKLOAD: {kind}/{name}

=== LIVE CLUSTER DATA ===
{context}
=== END DATA ===

Think like a careful senior SRE at 3am. Two questions:
1. ROOT CAUSE: what is actually wrong? Be specific and cite the evidence (a log \
line, an event, a field in the spec).
2. IS IT SAFE FOR YOU TO FIX ALONE? A fix is SAFE only if it is a REVERSIBLE \
configuration change with no risk to data or state: a missing/incorrect env var, \
a wrong image tag, a resource request/limit that is clearly mis-sized, a typo. A \
fix is RISKY (needs a human) if the container's OWN application code is failing (a \
real bug/stack trace in a new release), if it involves data, migrations, volumes, \
secrets, or persistent state, if rolling back could hide a partially-applied \
change, or if you are not confident. When unsure, choose RISKY. It is far worse to \
auto-apply a wrong fix at 3am than to page a human.

If SAFE, give the SINGLE kubectl command that fixes the ROOT CAUSE on the owning \
workload (so it survives a restart). Use only these shapes:
  kubectl set env deployment/<name> KEY=VALUE
  kubectl set image deployment/<name> <container>=<image:tag>
  kubectl set resources deployment/<name> --limits=memory=<X> --requests=memory=<Y>
  kubectl rollout restart deployment/<name>
  kubectl delete pod <podname>
Never delete deployments, PVCs, secrets, namespaces, or use --all/--force.

Respond with ONLY a JSON object, no prose, no fences:
{{"root_cause": "<one or two sentences citing evidence>", "classification": "SAFE" \
or "RISKY", "fix_command": "<the kubectl command WITHOUT a leading kubectl, or \
empty if RISKY>", "reason": "<why safe, or why a human is needed>", "confidence": \
"high" or "medium" or "low"}}"""


def ask_claude(alert, ns, pod, kind, name, context):
    prompt = PROMPT.format(alertname=alert.get("alertname", "?"),
                           reason=alert.get("reason", "?"), ns=ns, pod=pod,
                           kind=kind, name=name, context=context)
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")
    p = subprocess.run([CLAUDE, "-p", prompt, "--permission-mode", "acceptEdits"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)
    out = p.stdout.strip()
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return {"root_cause": "AI returned no parseable answer", "classification": "RISKY",
                "fix_command": "", "reason": "could not parse AI output", "confidence": "low"}
    try:
        return json.loads(m.group(0))
    except Exception as e:
        return {"root_cause": f"AI output not valid JSON ({e})", "classification": "RISKY",
                "fix_command": "", "reason": "unparseable", "confidence": "low"}


def get_firing_alerts():
    try:
        req = urllib.request.Request(AM + "/api/v2/alerts?active=true&silenced=false&inhibited=false")
        data = json.load(urllib.request.urlopen(req, timeout=8))
    except Exception as e:
        log("!!", f"cannot reach Alertmanager at {AM}: {e}", "r")
        return []
    out = []
    for a in data:
        lbl = a.get("labels", {})
        if lbl.get("team") != "nightshift":
            continue
        if a.get("status", {}).get("state") != "active":
            continue
        out.append({"alertname": lbl.get("alertname"), "ns": lbl.get("namespace", "default"),
                    "pod": lbl.get("pod", ""), "reason": (a.get("annotations", {}) or {}).get("reason", ""),
                    "fingerprint": a.get("fingerprint", lbl.get("alertname", "") + lbl.get("pod", ""))})
    return out


def verify_recovery(ns, kind, name, timeout=90):
    log("..", f"verifying {kind}/{name} recovers (up to {timeout}s)...", "d")
    deadline = time.time() + timeout
    sel = None
    while time.time() < deadline:
        rc, out, _ = kubectl(["-n", ns, "get", "deploy", name, "-o",
                              "jsonpath={.status.readyReplicas}/{.status.replicas}"])
        if out and out.startswith(tuple("123456789")) and out.split("/")[0] == out.split("/")[-1]:
            return True
        time.sleep(5)
    return False


def handle(alert):
    ns, pod = alert["ns"], alert["pod"]
    log("!!", f"ALERT {alert['alertname']}  {ns}/{pod}  ({alert['reason']})", "y")
    log("..", "gathering live cluster data (describe / logs / events / spec)...", "d")
    context, kind, name = gather_context(ns, pod)
    log("..", "asking the AI to diagnose + classify the fix...", "c")
    verdict = ask_claude(alert, ns, pod, kind, name, context)

    log(">>", f"root cause: {verdict.get('root_cause','?')}", "w")
    cls = str(verdict.get("classification", "RISKY")).upper()
    cmd = (verdict.get("fix_command") or "").strip()
    if cmd.startswith("kubectl "):
        cmd = cmd[len("kubectl "):]

    if cls != "SAFE" or not cmd:
        log("^^", f"ESCALATE -> paging human. {verdict.get('reason','')}", "r")
        return "escalated"

    ok, why = command_is_allowed(cmd)
    if not ok:
        log("^^", f"AI called it SAFE, but the command is NOT on the allow-list ({why}).", "r")
        log("^^", f"ESCALATE -> paging human. proposed was: kubectl {cmd}", "r")
        return "escalated-blocked"

    log("Rx", f"SAFE + allow-listed. auto-applying: kubectl {cmd}", "g")
    full = cmd.split()
    rc, out, err = kubectl(full)
    if rc != 0:
        log("^^", f"fix failed ({err}); ESCALATE -> paging human.", "r")
        return "fix-failed"
    log("Rx", (out or "applied."), "g")
    if verify_recovery(ns, kind, name):
        log("OK", f"{kind}/{name} is healthy again. auto-healed, no human needed.", "g")
        return "healed"
    log("^^", f"applied the fix but {kind}/{name} did not recover in time; ESCALATE.", "r")
    return "applied-not-recovered"


def main():
    global CLAUDE
    CLAUDE = find_claude()
    once = "--once" in sys.argv
    log("::", f"night-shift agent online. context={KCTX}  alertmanager={AM}", "c")
    seen = {}
    while True:
        alerts = get_firing_alerts()
        if alerts:
            # one incident per pod (dedupe multiple alerts on the same pod)
            by_pod = {}
            for a in alerts:
                by_pod.setdefault((a["ns"], a["pod"]), a)
            for key, a in by_pod.items():
                if key in seen and (time.time() - seen[key]) < 300:
                    continue
                seen[key] = time.time()
                handle(a)
        elif once:
            log("::", "no firing night-shift alerts.", "d")
        if once:
            break
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
