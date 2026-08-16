"""Host-side LLM bridge for the in-cluster night-shift agent (hybrid, $0).

The agent runs as a pod in the cluster, but the AI reasoning uses the Claude
subscription CLI, which lives on this host. So the pod POSTs its prompt here and
this tiny server shells out to `claude -p` and returns the answer. That keeps the
whole thing $0 (no API key) while the agent itself is a real in-cluster workload.

  POST /diagnose  {"prompt": "..."}  ->  {"response": "<model text>"}
  GET  /health                       ->  ok

Run on the host:  python llm_bridge.py   (listens on 0.0.0.0:8799)
"""
import glob
import json
import os
import shutil
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("BRIDGE_PORT", "8799"))


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


CLAUDE = find_claude()


def ask(prompt):
    env = dict(os.environ)
    env["PATH"] = r"C:\Program Files\Git\cmd;" + env.get("PATH", "")
    p = subprocess.run([CLAUDE, "-p", prompt, "--permission-mode", "acceptEdits"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=180)
    return p.stdout.strip()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True, "claude": os.path.basename(CLAUDE)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/diagnose":
            self._send(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
            prompt = req.get("prompt", "")
            if not prompt:
                self._send(400, {"error": "no prompt"})
                return
            print(f"[bridge] diagnose ({len(prompt)} chars)...", flush=True)
            out = ask(prompt)
            print(f"[bridge]   -> {len(out)} chars", flush=True)
            self._send(200, {"response": out})
        except Exception as e:
            self._send(500, {"error": str(e)})


if __name__ == "__main__":
    print(f"LLM bridge on 0.0.0.0:{PORT}  (claude: {CLAUDE})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
