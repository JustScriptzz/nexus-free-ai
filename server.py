"""Free AI website backend - self-hosted, no logs, stdlib only.
Runs on Kaggle next to KaggleUI, proxies to KaggleUI locally so the API key never reaches the browser.

Privacy: no request bodies logged, no chat history written to disk, Cache-Control: no-store.
Safety: /api/exec only runs restricted Python in an isolated temp dir, wiped after each run.
        No shell, no sudo, no access outside the sandbox.
"""
import ast
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
INDEX = ROOT / "index.html"

def load_kui():
    home = os.environ.get("KAGGLEUI_HOME", "/tmp/kaggleui")
    state_file = os.path.join(home, "state.json")
    base = os.environ.get("KAGGLEUI_BASE", "")
    api_key = os.environ.get("KAGGLEUI_API_KEY", "")
    port = int(os.environ.get("KAGGLEUI_PORT", "8890"))
    try:
        st = json.loads(open(state_file).read())
        port = int(st.get("port", port))
        api_key = api_key or st.get("api_key", "")
    except Exception:
        pass
    if not base:
        base = f"http://127.0.0.1:{port}"
    return base.rstrip("/"), api_key

KUI_BASE, KUI_KEY = load_kui()

# Admin-locked models: users never pick. Set these envs on Kaggle backend only.
# Frontend sends no model names; backend forces these. Keeps deployment private.
FIXED = {
    "chat": os.environ.get("FIXED_TEXT_MODEL", ""),
    "image": os.environ.get("FIXED_IMAGE_MODEL", ""),
    "tts": os.environ.get("FIXED_TTS_MODEL", ""),
    "stt": os.environ.get("FIXED_STT_MODEL", ""),
    "video": os.environ.get("FIXED_VIDEO_MODEL", ""),
}
# Admin-locked display names + system prompt. Users see names, never weights.
NAMES = {
    "site": os.environ.get("SITE_NAME", "free-ai"),
    "chat": os.environ.get("CHAT_NAME", "Chat"),
    "image": os.environ.get("IMAGE_NAME", "Image"),
    "tts": os.environ.get("TTS_NAME", "Voice"),
    "stt": os.environ.get("STT_NAME", "Transcribe"),
}
FIXED_SYSTEM = os.environ.get("FIXED_SYSTEM_PROMPT", "")

# Always-on SFW gate for image prompts. No toggle to disable.
# Keeps local T4 runs lawful: blocks explicit sexual content and any
# sexualization of minors. KaggleUI runners also keep their own safety-checker on.
BLOCKED_IMAGE_TERMS = [
    "nsfw", "porn", "explicit", "erotic", "sexual", "nude", "naked",
    "undressed", "strip", "fetish", "orgasm", "intercourse", "xxx",
    "rape", "non-consensual", "forced", "bestiality", "incest",
]
BLOCKED_MINOR_HINTS = [
    "child", "minor", "kid", "toddler", "baby", "teen",
    "schoolgirl", "schoolboy", "young",
]

def blocked_image_prompt(prompt: str):
    low = (prompt or "").lower()
    for t in BLOCKED_IMAGE_TERMS:
        if t in low:
            return True, f"blocked sexual/explicit term: {t}"
    # block any prompt mixing minor hints with sexual context words
    if any(m in low for m in BLOCKED_MINOR_HINTS):
        for s in ["nude", "naked", "sexual", "explicit", "nsfw", "porn",
                  "erotic", "kiss", "bed", "shower", "bikini", "lingerie"]:
            if s in low:
                return True, "blocked: minor + sexual context"
    return False, "ok"

def kui_request(path, method="GET", body=None, content_type="application/json", extra_headers=None):
    """Forward to KaggleUI. Returns (status, resp_content_type, data_bytes)."""
    url = KUI_BASE + path
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", content_type)
    if KUI_KEY:
        req.add_header("Authorization", f"Bearer {KUI_KEY}")
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, "application/json", e.read()
    except Exception as e:
        return 502, "application/json", json.dumps({"error": f"kaggleui unreachable at {url}: {e}. Is a model deployed and Ready?"}).encode()

# ---------- sandboxed python exec ----------
ALLOWED_IMPORTS = {"math", "random", "datetime", "json", "re", "collections",
                   "itertools", "statistics", "decimal", "fractions", "typing"}
BLOCKED_NAMES = {"open", "eval", "exec", "compile", "__import__", "globals", "locals",
                 "vars", "dir", "help", "input", "exit", "quit", "breakpoint",
                 "memoryview", "os", "sys", "subprocess", "socket", "shutil",
                 "pathlib", "pty", "importlib"}
BLOCKED_ATTRS = {"system", "popen", "remove", "rmtree", "unlink", "mkdir", "makedirs",
                 "chdir", "chmod", "chown", "kill", "fork", "execv", "spawn",
                 "socket", "connect", "bind", "run", "call", "check_output"}

def check_code_safe(code: str):
    if len(code) > 8000:
        return False, "code too long (max 8000 chars)"
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"syntax error: {e}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import,)):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    return False, f"import blocked: {a.name} (allowed: {sorted(ALLOWED_IMPORTS)})"
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod not in ALLOWED_IMPORTS:
                return False, f"import blocked: {node.module}"
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES or node.id.startswith("__"):
                return False, f"blocked name: {node.id}"
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRS or node.attr.startswith("__"):
                return False, f"blocked attribute: {node.attr}"
    # string blocklist for shell-escape attempts
    low = code.lower()
    for bad in ["sudo", "rm -rf", "rm ", "mkfs", "dd if=", ":(){", "shutdown",
                "reboot", "chown", "/etc/passwd", "/etc/shadow"]:
        if bad in low:
            return False, f"blocked pattern: {bad}"
    return True, "ok"

def run_sandboxed(code: str):
    ok, msg = check_code_safe(code)
    if not ok:
        return {"ok": False, "error": msg}
    tmp = tempfile.mkdtemp(prefix="ai-sandbox-")
    try:
        (Path(tmp) / "main.py").write_text(code)
        p = subprocess.run(
            [sys.executable, "-I", "-E", "main.py"],
            cwd=tmp, capture_output=True, text=True, timeout=8,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0",
                 "PYTHONIOENCODING": "utf-8"},
        )
        out = (p.stdout or "")[-6000:]
        err = (p.stderr or "")[-2000:]
        return {"ok": p.returncode == 0, "stdout": out, "stderr": err,
                "returncode": p.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout after 8s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # privacy: never log URLs with prompts or bodies, only errors to stderr
        if "api" not in fmt:
            sys.stderr.write("%s\n" % (fmt % args))

    def send_json(self, obj, status=200):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def serve_index(self):
        try:
            b = INDEX.read_bytes()
        except Exception:
            b = b"<h1>index.html missing</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' data: blob:; connect-src 'self';")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            return self.serve_index()
        if self.path == "/api/health":
            s, _, b = kui_request("/v1/models")
            try:
                n = len(json.loads(b).get("data", []))
            except Exception:
                n = -1
            return self.send_json({"site": "ok", "ready": s == 200,
                                   "count": n, "no_logs": True})
        if self.path == "/api/models":
            # do not leak deployment names to users; only readiness
            s, _, b = kui_request("/v1/models")
            try:
                n = len(json.loads(b).get("data", []))
            except Exception:
                n = -1
            return self.send_json({"ready": s == 200, "count": n})
        return self.send_json({"error": "not found"}, 404)

    def read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except Exception:
            return {}

    def proxy_json(self, kui_path, body=None):
        if body is None:
            body = self.read_json()
        s, _, b = kui_request(kui_path, "POST", body)
        try:
            obj = json.loads(b)
        except Exception:
            obj = {"raw": b[:4000].decode("utf-8", "replace")}
        return self.send_json(obj, s if s < 500 else 502)

    def do_POST(self):
        if self.path == "/api/chat":
            body = self.read_json()
            if FIXED["chat"]:
                body["model"] = FIXED["chat"]
            elif not body.get("model"):
                return self.send_json({"error": "text model not configured by admin"}, 503)
            # force admin system prompt, drop any client-supplied system to keep behavior consistent
            msgs = [m for m in body.get("messages", []) if m.get("role") != "system"]
            if FIXED_SYSTEM:
                msgs = [{"role": "system", "content": FIXED_SYSTEM}] + msgs
            body["messages"] = msgs
            # first pass to Kui (reasoning_effort / think / tools pass through as-is)
            s, _, b = kui_request("/v1/chat/completions", "POST", body)
            try:
                obj = json.loads(b)
            except Exception:
                return self.send_json({"raw": b[:4000].decode("utf-8", "replace")}, 502)
            # one-step tool loop for sandboxed python (Claude-like terminal)
            try:
                msg = obj.get("choices", [{}])[0].get("message", {})
                calls = msg.get("tool_calls") or []
                for tc in calls[:2]:
                    fn = (tc.get("function") or {})
                    if fn.get("name") == "run_python":
                        try:
                            args = json.loads(fn.get("arguments") or "{}")
                        except Exception:
                            args = {}
                        res = run_sandboxed(str(args.get("code", ""))[:8000])
                        body["messages"] = body.get("messages", []) + [
                            {"role": "assistant", "content": None, "tool_calls": [tc]},
                            {"role": "tool", "tool_call_id": tc.get("id"),
                             "content": json.dumps(res)[:6000]},
                        ]
                        s2, _, b2 = kui_request("/v1/chat/completions", "POST", body)
                        try:
                            obj = json.loads(b2)
                        except Exception:
                            pass
                        break
            except Exception:
                pass
            return self.send_json(obj, s if s < 500 else 502)
        if self.path == "/api/image":
            body = self.read_json()
            if FIXED["image"]:
                body["model"] = FIXED["image"]
            elif not body.get("model"):
                return self.send_json({"error": "image model not configured by admin"}, 503)
            prompt = str(body.get("prompt", "") or "")
            blocked, reason = blocked_image_prompt(prompt)
            if blocked:
                return self.send_json({"error": f"SFW filter: {reason}. Explicit sexual content is not allowed."}, 400)
            return self.proxy_json("/v1/images/generations", body)
        if self.path == "/api/video":
            body = self.read_json()
            if FIXED["video"]:
                body["model"] = FIXED["video"]
            elif not body.get("model"):
                return self.send_json({"error": "video model not configured by admin"}, 503)
            prompt = str(body.get("prompt", "") or "")
            blocked, reason = blocked_image_prompt(prompt)
            if blocked:
                return self.send_json({"error": f"SFW filter: {reason}."}, 400)
            return self.proxy_json("/v1/videos", body)
        if self.path == "/api/exec":
            body = self.read_json()
            code = str(body.get("code", ""))[:8000]
            return self.send_json(run_sandboxed(code))
        if self.path == "/api/tts":
            body = self.read_json()
            if FIXED["tts"]:
                body["model"] = FIXED["tts"]
            elif not body.get("model"):
                return self.send_json({"error": "tts model not configured by admin"}, 503)
            s, ct, b = kui_request("/v1/audio/speech", "POST", body)
            if s != 200:
                try:
                    return self.send_json(json.loads(b), s)
                except Exception:
                    return self.send_json({"error": b[:1000].decode("utf-8", "replace")}, s)
            self.send_response(200)
            self.send_header("Content-Type", ct or "audio/mpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if self.path == "/api/stt":
            # frontend sends {audio_b64, filename}; model forced admin-side
            body = self.read_json()
            try:
                audio = base64.b64decode(body.get("audio_b64", ""))
            except Exception:
                return self.send_json({"error": "bad audio_b64"}, 400)
            model = FIXED["stt"] or str(body.get("model", ""))
            if not model:
                return self.send_json({"error": "stt model not configured by admin"}, 503)
            fname = str(body.get("filename", "audio.webm"))[:80]
            boundary = "----aiwebsite1234"
            parts = []
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{model}\r\n".encode())
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + audio + b"\r\n")
            parts.append(f"--{boundary}--\r\n".encode())
            mp = b"".join(parts)
            s, _, b = kui_request("/v1/audio/transcriptions", "POST", mp,
                                  content_type=f"multipart/form-data; boundary={boundary}")
            try:
                return self.send_json(json.loads(b), s)
            except Exception:
                return self.send_json({"error": b[:2000].decode("utf-8", "replace")}, s)
        return self.send_json({"error": "not found"}, 404)

if __name__ == "__main__":
    port = int(os.environ.get("SITE_PORT", "8000"))
    print(f"Free AI site on http://127.0.0.1:{port} -> Kui {KUI_BASE} (key {'set' if KUI_KEY else 'MISSING'})", flush=True)
    print("No logs, no disk history. Exec sandbox: python-only, 8s timeout, temp dir wiped.", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
