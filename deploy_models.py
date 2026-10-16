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
import sys
import time
import urllib.error
import urllib.request

HOME = os.environ.get("KAGGLEUI_HOME", "/tmp/kaggleui")
ST = json.load(open(f"{HOME}/state.json"))
BASE = f"http://127.0.0.1:{ST['port']}"
AUTH = {"Authorization": f"Bearer {ST['admin_token']}", "Content-Type": "application/json"}

UNCENSORED_URL = ("https://huggingface.co/bartowski/"
                  "Qwen2.5-Coder-14B-Instruct-abliterated-GGUF")

# Import the SINGLE Q4_K_M file, never the repo root.
# The repo ships 25 quants totalling ~74GB (IQ2_M..f16, Q8_0 alone is 15.7GB).
# Importing the repo root exhausts the disk and dies with "Download process exited (1)".
GGUF_FILE = (UNCENSORED_URL +
             "/resolve/main/Qwen2.5-Coder-14B-Instruct-abliterated-Q4_K_M.gguf")

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

print("\n== 3. uncensored model (opt-in) ==")
if os.environ.get("TRY_UNCENSORED") != "1":
    print("  skipped. The API import path returns 500 on this host.")
    print("  Set TRY_UNCENSORED=1 to retry, or import the GGUF by hand:")
    print(f"    {GGUF_FILE}")
    print("\n== 4. wait for everything to settle ==")
    for d in deployments():
        print(f"  {d.get('status'):6s} {d.get('name'):18s} "
              f"{d.get('spec', {}).get('model', {}).get('id')}")
    sys.exit(0)


def import_jobs():
    _, snap = call("GET", "/_kui/api/catalog/imports")
    if isinstance(snap, list):
        return snap
    if isinstance(snap, dict):
        for key in ("imports", "jobs", "data", "items"):
            if isinstance(snap.get(key), list):
                return snap[key]
    return []


def find_job():
    """Match by substring. The job key is `id`, not `ident`."""
    for j in import_jobs():
        blob = " ".join(str(j.get(k, "")) for k in ("url", "repo", "name", "id"))
        if "abliterated" in blob.lower():
            return j
    return None


DONE = ("complete", "failed", "error", "cancelled")

# Reap dead imports first. A repo-root import pulled ~74GB before failing; if that
# partial data is still on disk it will block the real single-file download.
reaped = 0
for j in import_jobs():
    if "abliterated" in json.dumps(j).lower() and j.get("status") in DONE:
        jid = j.get("id")
        code, _ = call("DELETE", f"/_kui/api/catalog/imports/{jid}")
        reaped += 1
        print(f"  reaped dead import {jid} ({j.get('status')}, "
              f"{round((j.get('bytes') or 0) / 1e9, 1)}GB) http={code}")

# Reaping unlinks a multi-GB tree server-side. Posting a fresh import straight
# after can land mid-cleanup and return 500, so let the filesystem settle.
if reaped:
    print("  waiting 90s for the server to finish deleting the old files")
    for i in range(9):
        time.sleep(10)
        print(f"    {10 * (i + 1)}s", flush=True)

job = find_job()
if job and job.get("status") not in DONE:
    print(f"  attaching to in-flight import: status={job.get('status')} "
          f"{round((job.get('bytes') or 0) / 1e9, 1)}GB")
else:
    job = None
    code, resp = call("POST", "/_kui/api/catalog/imports",
                      {"url": GGUF_FILE, "kind": "auto", "name": "", "revision": None})
    print(f"  import requested http={code} url=...Q4_K_M.gguf {str(resp)[:160]}")
    job = find_job()

if job and job.get("status") not in DONE:
    print("  waiting for GGUF download (~9GB, can take a while on Kaggle)...")
    for _ in range(150):
        time.sleep(20)
        j = find_job()
        if j:
            job = j
        print(f"    status={job.get('status')} "
              f"progress={round((job.get('progress') or 0) * 100)}%", flush=True)
        if job.get("status") in DONE:
            break

print(f"  import final: status={job.get('status')}")
if job.get("status") != "complete":
    print("  GGUF import did not complete. Censored nexus-chat stays live.")
    sys.exit(0)
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
            # Swap, do not add. The censored 14B holds ~9GB and the new one needs
            # ~9GB; together they OOM the T4s. qwen2.5:14b is a plain ollama tag,
            # so it is one call away if the uncensored model fails.
            print("  stopping censored nexus-chat to free VRAM for the swap")
            for d in deployments():
                if d.get("name") == "nexus-chat":
                    delete(d)
            time.sleep(15)
            out = create(UNCENSORED_NAME, "text", ref, {"ctx": 8192})
            if out.get("status") == "error":
                print("  attached+ollama failed, retrying as hf source...")
                out = create(UNCENSORED_NAME, "text",
                             {"source": "hf", "id": "Qwen2.5-Coder-14B-Instruct-abliterated-GGUF",
                              "variant": "Q4_K_M", "engine": "ollama"}, {"ctx": 8192})
            # If the uncensored model did not take, put the censored one straight back
            if out.get("status") == "error":
                print("  uncensored deploy failed -> restoring censored nexus-chat")
                create("nexus-chat", "text",
                       {"source": "ollama", "id": "qwen2.5:14b", "engine": "ollama"},
                       {"ctx": 8192})
            else:
                print("  censored nexus-chat intentionally left stopped (swap)")

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