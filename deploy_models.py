import json, os, urllib.request, time

HOME = os.environ.get("KAGGLEUI_HOME", "/tmp/kaggleui")
st = json.load(open(f"{HOME}/state.json"))
PORT = st["port"]
ADMIN = st["admin_token"]
BASE = f"http://127.0.0.1:{PORT}"

# Uncensored coder model, Q4_K_M ~9GB, Apache-2.0, ungated (no HF_TOKEN needed).
# Runs on the ollama engine, split across both T4s. Falls back to qwen2.5:14b (censored).
TEXT_PRIMARY = {"source": "hf", "id": "bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF",
                "variant": "Q4_K_M", "engine": "ollama"}
TEXT_FALLBACK = {"source": "ollama", "id": "qwen2.5:14b", "engine": "ollama"}

DEPLOYMENTS = [
    ("text", "nexus-chat", TEXT_PRIMARY, {"ctx": 8192}),
    ("image", "nexus-forge", {"source": "hf", "id": "stabilityai/sdxl-turbo", "engine": "diffusers"},
     {"dtype": "float16"}),
    ("tts", "nexus-voice", {"source": "hf", "id": "hexgrad/Kokoro-82M", "engine": "kokoro"}, {}),
    ("stt", "nexus-ear", {"source": "hf", "id": "Systran/faster-whisper-small",
                           "engine": "faster_whisper"}, {}),
]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {ADMIN}")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except Exception as e:
        return 0, {"error": str(e)}


created = []
for modality, name, model, options in DEPLOYMENTS:
    spec = {"modality": modality, "model": model, "name": name,
            "interface": "studio", "options": options}
    code, out = call("POST", "/_kui/api/deployments", spec)
    # if the GGUF variant is rejected by the engine, retry the plain ollama tag
    if name == "nexus-chat" and (code >= 400 or out.get("status") == "error"):
        print(f"  primary text spec rejected (http={code}); falling back")
        spec["model"] = TEXT_FALLBACK
        code, out = call("POST", "/_kui/api/deployments", spec)
    created.append(out.get("id", ""))
    print(f"CREATE {name:14s} http={code} id={out.get('id','')} {out.get('status','')} "
          f"{str(out.get('message',''))[:100]}")

print("\nwaiting for deployments to become ready (installs + downloads take a while)...")
deadline = time.time() + 1500
while time.time() < deadline:
    code, deps = call("GET", "/_kui/api/deployments")
    rows = deps if isinstance(deps, list) else deps.get("deployments", [])
    summary = " | ".join(
        f"{d.get('name')}={d.get('status')} {round((d.get('progress') or 0) * 100)}%"
        for d in rows)
    print(f"[{int(time.time()) % 100000}] {summary}", flush=True)
    if rows and all(d.get("status") == "ready" for d in rows):
        print("\nALL READY")
        break
    time.sleep(20)
else:
    print("\nTIMEOUT - check Deployments panel for failures")