"""Deploy the Nexus model set on Kaggle, idempotently.

Run INSIDE the Kaggle kernel after kaggleui.launch():
    !cd /kaggle/working/free-ai-website && git pull -q && python3 deploy_models.py

Design notes (learned the hard way):
  * Every target name is deleted before creation, so re-running NEVER stacks
    duplicates. Stacking was what blew past 30GB and made models error at 0%.
  * It never deletes a deployment that is currently `ready` unless that name is
    the exact one it is about to recreate. A working site survives a re-run.
  * The uncensored GGUF needs the Imports step first: Ollama cannot pull a GGUF
    straight from HuggingFace, it needs the file on disk. That is why the
    one-shot `source: hf` + `engine: ollama` spec failed at 0%.
  * If the uncensored deploy fails, the censored `qwen2.5:14b` stays live, so
    chat keeps working either way.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

HOME = os.environ.get("KAGGLEUI_HOME", "/tmp/kaggleui")
ST = json.load(open(f"{HOME}/state.json"))
BASE = f"http://127.0.0.1:{ST['port']}"
AUTH = {"Authorization": f"Bearer {ST['admin_token']}", "Content-Type": "application/json"}

UNCENSORED_URL = ("https://huggingface.co/bartowski/"
                  "Qwen2.5-Coder-14B-Instruct-abliterated-GGUF")

# name -> (modality, model_ref, options). Deleted-then-recreated each run.
CORE = [
    ("nexus-forge", "image",
     {"source": "hf", "id": "stabilityai/sdxl-turbo", "engine": "diffusers"},
     {"dtype": "float16"}),
    ("nexus-voice", "tts",
     {"source": "hf", "id": "hexgrad/Kokoro-82M", "engine": "kokoro"}, {}),
    ("nexus-ear", "stt",
     {"source": "hf", "id": "Systran/faster-whisper-small", "engine": "faster_whisper"}, {}),
    ("nexus-chat", "text",
     {"source": "ollama", "id": "qwen2.5:14b", "engine": "ollama"}, {"ctx": 8192}),
]
UNCENSORED_NAME = "nexus-uncensored"


def call(method, path, body=None, timeout=180):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers=AUTH)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except Exception:
            return e.code, {"error": raw[:300].decode("utf-8", "replace")}
    except Exception as e:
        return 0, {"error": str(e)}


def deployments():
    _, out = call("GET", "/_kui/api/deployments")
    rows = out if isinstance(out, list) else out.get("deployments", [])
    return rows if isinstance(rows, list) else []


def delete(d):
    code, out = call("DELETE", f"/_kui/api/deployments/{d['id']}")
    print(f"  DELETE {d.get('name')} ({d.get('status')}) http={code}")


def create(name, modality, model, options):
    spec = {"modality": modality, "model": model, "name": name,
            "interface": "studio", "options": options}
    code, out = call("POST", "/_kui/api/deployments", spec)
    print(f"  CREATE {name} http={code} status={out.get('status')} "
          f"{str(out.get('message', ''))[:120]}")
    return out


def is_duplicate(name, base_names):
    m = re.match(r"^(.*)-(\d+)$", name or "")
    return bool(m) and m.group(1) in base_names


print("== 1. sweep duplicates left by earlier runs ==")
base_names = {n for n, *_ in CORE} | {UNCENSORED_NAME}
for d in deployments():
    if is_duplicate(d.get("name"), base_names):
        delete(d)

print("\n== 2. ensure the four core deployments ==")
have = {d.get("name"): d for d in deployments()}
for name, modality, model, options in CORE:
    cur = have.get(name)
    if cur and cur.get("status") == "ready":
        print(f"  OK {name} already ready")
        continue
    if cur:
        delete(cur)
    create(name, modality, model, options)

print("\n== 3. uncensored model: import the GGUF, then deploy it ==")
_, imports = call("GET", "/_kui/api/catalog/imports")
if not isinstance(imports, dict):
    imports = {}
existing = None
for job in (imports.get("imports") or imports.get("jobs") or imports.get("data") or []):
    if UNCENSORED_URL in str(job.get("url", "")) and job.get("status") == "complete":
        existing = job
        break

if existing:
    print(f"  GGUF already imported: {existing.get('ident')}")
    job = existing
else:
    code, job = call("POST", "/_kui/api/catalog/imports",
                     {"url": UNCENSORED_URL, "kind": "auto", "name": "", "revision": None})
    print(f"  import requested http={code} ident={job.get('ident')} {str(job)[:160]}")
    ident = job.get("ident")
    if ident:
        print("  waiting for GGUF download (~9GB)...")
        for _ in range(90):
            time.sleep(20)
            _, snap = call("GET", "/_kui/api/catalog/imports")
            for j in (snap.get("imports") or snap.get("jobs") or snap.get("data") or []):
                if j.get("ident") == ident:
                    job = j
                    break
            print(f"    status={job.get('status')} "
                  f"progress={round((job.get('progress') or 0) * 100)}%", flush=True)
            if job.get("status") in ("complete", "failed", "error", "cancelled"):
                break

print(f"  import final: status={job.get('status')}")
if job.get("status") != "complete":
    print("  GGUF import did not complete. Censored nexus-chat stays live.")
else:
    models = job.get("models") or []
    print(f"  detected {len(models)} model file(s): "
          f"{[m.get('path', '?').split('/')[-1] for m in models][:4]}")
    have = {d.get("name"): d for d in deployments()}
    cur = have.get(UNCENSORED_NAME)
    if cur and cur.get("status") == "ready":
        print(f"  OK {UNCENSORED_NAME} already ready")
    else:
        if cur:
            delete(cur)
        ref = None
        if models:
            ref = {"source": "attached", "id": models[0].get("path"),
                   "engine": "ollama"}
        if ref:
            out = create(UNCENSORED_NAME, "text", ref, {"ctx": 8192})
            if out.get("status") == "error":
                print("  attached+ollama failed, retrying as hf source...")
                create(UNCENSORED_NAME, "text",
                       {"source": "hf", "id": UNCENSORED_URL.split("/")[-1],
                        "engine": "ollama"}, {"ctx": 8192})

print("\n== 4. wait for everything to settle ==")
deadline = time.time() + 1800
while time.time() < deadline:
    rows = deployments()
    line = " | ".join(f"{d.get('name')}={d.get('status')}" for d in rows)
    print(f"  {line}", flush=True)
    if rows and all(d.get("status") in ("ready", "error") for d in rows):
        break
    time.sleep(20)

print("\n== summary ==")
for d in deployments():
    print(f"  {d.get('status'):6s} {d.get('name'):18s} {d.get('spec', {}).get('model', {}).get('id')}")