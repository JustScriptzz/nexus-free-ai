"""molab setup for KaggleUI on an RTX PRO 6000 Blackwell (96GB VRAM).

Run each block in its own cell, in order.

WHY THIS DIFFERS FROM THE KAGGLE VERSION
  * launch(block=False) - molab is a reactive notebook, so a blocking launch()
    would freeze the UI. We background it and print the link ourselves.
  * No secrets in code. molab notebooks are PUBLIC. Never paste a token here.
    Cloudflare's quick tunnel needs no token at all, which is why it is the default.
  * Weights live in a writable dir; on a notebook VM /tmp is usually fine.

BEFORE YOU START: attach the GPU. In molab, use the notebook specs button in the
app header and select the RTX PRO 6000 Blackwell. Without it everything below
still runs, just on CPU.
"""

# ---------------------------------------------------------------- 1. install
# kaggleui is NOT on PyPI - it ships as a GitHub release wheel.
CELL_INSTALL = """
!pip install -q --no-cache-dir https://github.com/UltraFEmotes/KaggleUI/releases/download/v0.2.9/kaggleui-0.2.9-py3-none-any.whl
!pip install -q --no-cache-dir zstandard
"""

# Ollama's own installer needs the zstd CLI, not just the Python lib.
CELL_ZSTD = """
!apt-get install -y -qq zstd 2>/dev/null || (which zstd || echo "NO_ZSTD_CLI")
"""

# ---------------------------------------------------------------- 2. launch
# block=False so the notebook UI stays responsive.
CELL_LAUNCH = """
import os
os.environ.setdefault("KAGGLEUI_HOME", os.path.expanduser("~/.cache/kaggleui"))

import kaggleui
st = kaggleui.launch(block=False)

print()
print("=" * 62)
print("CONTROL PANEL :", st["url"])
print("ADMIN TOKEN  :", st["admin_token"])
print("API KEY      :", st.get("api_key") or "(api disabled)")
print("=" * 62)
print("Save that panel URL. It is your public site.")
"""

# ---------------------------------------------------------------- 3. check GPU
CELL_GPU = """
import subprocess
print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                      "--format=csv"], capture_output=True, text=True).stdout)
"""

# ---------------------------------------------------------------- 4. keepalive
# molab sessions are 12h, but the tunnel and idle kernels can still drop.
# Runs in the foreground of its own cell; the cell is the process.
CELL_KEEPALIVE = """
import json, os, time, urllib.request

HOME = os.environ.get("KAGGLEUI_HOME", os.path.expanduser("~/.cache/kaggleui"))
STATE = os.path.join(HOME, "state.json")


def _state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


while True:
    st = _state()
    port, tok = st.get("port"), st.get("admin_token", "")
    if port:
        base = f"http://127.0.0.1:{port}"
        try:
            urllib.request.urlopen(urllib.request.Request(
                base + "/_kui/api/session",
                headers={"Authorization": f"Bearer {tok}"}), timeout=15)
            print(f"[keepalive] ok url={(st.get('url') or '').split('?')[0]}", flush=True)
        except Exception as e:
            print(f"[keepalive] {type(e).__name__}", flush=True)
    time.sleep(45)
"""

# ---------------------------------------------------------------- 5. deploy
# With 96GB you can be generous. Suggested set (~60GB):
#   text   qwen2.5:32b            ~20GB
#   image  stabilityai/sdxl-turbo  ~7GB   (swap for FLUX.1-dev ~24GB if ungated+licensed)
#   tts    hexgrad/Kokoro-82M      ~1GB
#   stt    Systran/faster-whisper-small ~1GB
# Deploy from the panel UI (Deploy model) rather than a script - simpler, and it
# shows you VRAM/disk fit per model before you commit.
CELL_LIST = """
import json, urllib.request
st = json.load(open(os.path.join(os.environ.get("KAGGLEUI_HOME",
                        os.path.expanduser("~/.cache/kaggleui")), "state.json")))
r = urllib.request.Request(
    f"http://127.0.0.1:{st['port']}/_kui/api/deployments",
    headers={"Authorization": "Bearer " + st["admin_token"]})
d = json.loads(urllib.request.urlopen(r, timeout=30).read())
for x in (d if isinstance(d, list) else d.get("deployments", [])):
    print(f"{x.get('status'):8s} {x.get('name')}")
"""

CELLS = {
    "1_install": CELL_INSTALL,
    "2_zstd": CELL_ZSTD,
    "3_launch": CELL_LAUNCH,
    "4_check_gpu": CELL_GPU,
    "5_keepalive": CELL_KEEPALIVE,
    "6_list_models": CELL_LIST,
}

if __name__ == "__main__":
    print("molab setup cells:")
    for k, v in CELLS.items():
        print(f"\n# ===== {k} " + "=" * 40)
        print(v.strip())