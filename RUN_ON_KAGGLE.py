"""Cell 2 for Kaggle: start the self-hosted site next to KaggleUI + expose it.
Paste this into a NEW notebook cell AFTER kaggleui.launch() and Run.

What it does:
 1. reads /tmp/kaggleui/state.json for port + api_key (this is how you 'get the key'
    without copy-paste: launch card + Access & API show kui-..., state.json holds it)
 2. starts free-ai-website/server.py on 127.0.0.1:8000 (no logs)
 3. opens a second cloudflare quick tunnel for :8000, prints YOUR-SITE link
Assumes you uploaded free-ai-website/ via Add-ons -> Attach data or !git clone it.
"""
import json, os, subprocess, time, sys, pathlib

SITE_DIR = "/kaggle/working/free-ai-website"  # change if you cloned elsewhere
KUI_HOME = os.environ.get("KAGGLEUI_HOME", "/tmp/kaggleui")

# 1. load credentials server-side only, never print secrets
st = json.loads(open(f"{KUI_HOME}/state.json").read())
public_url = str(st.get("url") or "").split("?")[0]  # strip ?token= admin token
print("KaggleUI ready:", "yes" if st.get("url") else "no", public_url)
print("Models Ready: deploy text/image/tts/stt in KaggleUI first. T4x2=~32GB total.")

# --- ADMIN: ONE of each, users never see these; backend forces them ---
FIXED_TEXT_MODEL = "dolphin-24b"   # <-- replace with your Ready uncensored deployment name
FIXED_IMAGE_MODEL = "sdxl-turbo"
FIXED_TTS_MODEL = "kokoro"
FIXED_STT_MODEL = "whisper-small"
FIXED_VIDEO_MODEL = ""  # disabled, no video

SITE_NAME = "Nexus"
CHAT_NAME = "Chat"
IMAGE_NAME = "Image"
TTS_NAME = "Voice"
STT_NAME = "Transcribe"

FIXED_SYSTEM_PROMPT = (
    "You are Nexus, a helpful assistant. Answer directly. "
    "Use run_python tool when math/code helps. "
    "Image is SFW-only. Stay lawful. No logs."
)

CLOUD_TEXT_MODEL = "gemini/gemini-3-flash"  # CLOUD MODEL - owner gateway, might log
LLMHUB_BASE = "https://llmhub.llmhub.workers.dev/v1"
# set Kaggle Secret LLMHUB_KEY (toggle ON for notebook); value never printed
# 2. start site backend (background, no request logging)
srv = subprocess.Popen([sys.executable, f"{SITE_DIR}/server.py"],
    cwd=SITE_DIR, env={**os.environ, "SITE_PORT": "8000",
    "KAGGLEUI_BASE": f"http://127.0.0.1:{st.get('port', 8890)}",
    "KAGGLEUI_API_KEY": st.get("api_key", ""),
    "KAGGLEUI_ADMIN_TOKEN": st.get("admin_token", ""),
    "FIXED_TEXT_MODEL": FIXED_TEXT_MODEL, "FIXED_IMAGE_MODEL": FIXED_IMAGE_MODEL,
    "FIXED_TTS_MODEL": FIXED_TTS_MODEL, "FIXED_STT_MODEL": FIXED_STT_MODEL,
    "FIXED_VIDEO_MODEL": FIXED_VIDEO_MODEL,
    "SITE_NAME": SITE_NAME, "CHAT_NAME": CHAT_NAME, "IMAGE_NAME": IMAGE_NAME,
    "TTS_NAME": TTS_NAME, "STT_NAME": STT_NAME,
    "FIXED_SYSTEM_PROMPT": FIXED_SYSTEM_PROMPT,
    "CLOUD_TEXT_MODEL": CLOUD_TEXT_MODEL, "LLMHUB_BASE": LLMHUB_BASE,
    "LLMHUB_KEY": os.environ.get("LLMHUB_KEY", "")},
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
time.sleep(2)
print("site backend pid:", srv.pid, "-> http://127.0.0.1:8000 (local to notebook)")

# 3. public tunnel for the site (separate from KaggleUI tunnel)
CF = "/tmp/cf-site/cloudflared"
# download to /tmp (executable), not /kaggle/working (noexec -> PermissionError)
if not os.path.exists(CF):
    os.makedirs("/tmp/cf-site", exist_ok=True)
    print("downloading cloudflared...")
    import urllib.request
    u = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
    urllib.request.urlretrieve(u, CF); os.chmod(CF, 0o755)
elif not os.access(CF, os.X_OK):
    os.chmod(CF, 0o755)

log = open("/tmp/cf-site.log", "w")
tun = subprocess.Popen([CF, "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate"],
    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
print("waiting for site tunnel...")
url = None
for _ in range(60):
    time.sleep(1)
    txt = open("/tmp/cf-site.log").read()
    import re
    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
    if m:
        url = m.group(0); break
print("YOUR SITE:", url or "not ready yet, check /tmp/cf-site.log")
print("Users get no model picker; you control weights via FIXED_* above. No chats stored, sandbox terminal python-only.")
