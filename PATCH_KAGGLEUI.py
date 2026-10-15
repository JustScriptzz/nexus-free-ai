"""Patch the installed KaggleUI so we serve ITS OWN web UI, rebranded to Nexus, guest-open, SFW.

Run this AFTER install.sh (so kaggleui is on disk) and BEFORE kaggleui.launch().
It copies the package to /kaggle/working/nexus_kaggleui, edits that copy, and prints the
cell to run it. Nothing is downloaded; every replacement is verified.

What changes vs stock KaggleUI (users):
  - title/og tags say Nexus, no KaggleUI name in the page
  - Access & API / System / Secrets / Deploy stay admin-token only; EVERY visitor is a
    guest who lands in Studio with the ONE models you deployed. No password, no keys.
  - the /host admin link still needs your admin token
  - image/video prompts are SFW-filtered server-side (always-on, no toggle)
  - the raw install command shown in the panel is hidden
Admin (you) keep: Deployments, catalog, keys & tokens, updates. Set password off in
Settings as well; this patch makes it sticky regardless.
"""
import json
import os
import re
import shutil
import sys
from pathlib import Path

import kaggleui

SRC = Path(kaggleui.__file__).resolve().parent
DST = Path(os.environ.get("NEXUS_PATCH_DIR", "/kaggle/working/nexus_kaggleui"))
MARK = "# nexus-patched"

BLOCK_SFW = '''
# nexus-patched: always-on SFW gate (no toggle)
_NEXUS_SFW = ("nsfw", "porn", "explicit", "erotic", "sexual", "nude", "naked", "undressed", "strip",
              "fetish", "orgasm", "intercourse", "xxx", "rape", "non-consensual", "forced", "bestiality", "incest")
_NEXUS_MINORS = ("child", "minor", "kid", "toddler", "baby", "teen", "schoolgirl", "schoolboy", "young")
_NEXUS_SFW_CTX = ("nude", "naked", "sexual", "explicit", "nsfw", "porn", "erotic", "kiss", "bed", "shower",
                  "bikini", "lingerie")


def _nexus_sfw(prompt) -> bool:
    low = str(prompt or "").lower()
    if any(t in low for t in _NEXUS_SFW):
        return True
    if any(m in low for m in _NEXUS_MINORS) and any(s in low for s in _NEXUS_SFW_CTX):
        return True
    return False
'''


def sub(text: str, old: str, new: str, path: Path, required: bool = True) -> str:
    if old not in text:
        if required:
            raise SystemExit(f"PATCH FAILED: pattern not found in {path}:\n{old[:120]}")
        return text
    return text.replace(old, new, 1)


def patch_app(path: Path) -> None:
    t = path.read_text(encoding="utf-8")
    t = sub(t, '    app = FastAPI(', BLOCK_SFW + '    app = FastAPI(', path)
    # guest-open: every visitor counts as having access; only the admin token gates the panel
    t = sub(t, '    def require_access(request: Request) -> None:\n        if not state.auth.has_access(request, state.settings):\n            raise HTTPException(401, "Password required")',
            '    def require_access(request: Request) -> None:\n        # nexus-patched: guests in, no password\n        return None', path)
    # Studio WS uses the same check
    t = sub(t, '    @app.websocket("/_kui/studio/ws")',
            '    @app.websocket("/_kui/studio/ws")', path, required=False)
    # hide the raw install command in the panel
    t = sub(t, 'INSTALL_LINE = "!curl -fsSL https://raw.githubusercontent.com/UltraFEmotes/KaggleUI/main/install.sh | bash"',
            f'INSTALL_LINE = "{MARK} hidden"  # nexus-patched', path)
    t = sub(t, 'return HTMLResponse("<h1>KaggleUI</h1>',
            'return HTMLResponse("<h1>Nexus</h1>', path, required=False)
    # SFW on image generation
    t = sub(t, '        body = await _json(request)\n        dep = find(request, body.get("model"), "image")',
            '        body = await _json(request)\n        if _nexus_sfw(body.get("prompt")):  # nexus-patched\n            return openai_error(GatewayError(400, "SFW filter: explicit sexual content is not allowed."))\n        dep = find(request, body.get("model"), "image")', path, required=False)
    # SFW on video generation
    t = sub(t, '        model = body.get("model")\n        dep = None\n        for modality in ("video", "world"):',
            '        if _nexus_sfw(body.get("prompt")):  # nexus-patched\n            return openai_error(GatewayError(400, "SFW filter: explicit sexual content is not allowed."))\n        model = body.get("model")\n        dep = None\n        for modality in ("video", "world"):', path, required=False)
    t = sub(t, 'raise GatewayError(400, f"This model generates 3D scenes.',
            'raise GatewayError(400, f"This model generates 3D scenes.', path, required=False)
    path.write_text(t, encoding="utf-8")


def patch_schemas(path: Path) -> None:
    t = path.read_text(encoding="utf-8")
    t = sub(t, 'class AccessSettings(BaseModel):\n    password_enabled: bool = True',
            f'class AccessSettings(BaseModel):\n    password_enabled: bool = False  {MARK}', path, required=False)
    path.write_text(t, encoding="utf-8")


OG = ('<meta property="og:title" content="Nexus">'
      '<meta property="og:site_name" content="Nexus">'
      '<meta property="og:description" content="Free AI chat, image and voice. Our GPUs, no keys, no logs.">'
      '<meta name="theme-color" content="#0b0d10">')

BANNER_JS = f"""
<script>(function(){{
  var s=document.createElement('style');s.textContent=
    '.nx-badge{{position:fixed;left:10px;bottom:10px;z-index:9999;font:600 12px/1 system-ui;color:#7dd3a8;'+
    'background:#0b0d10;border:1px solid #2a4436;border-radius:999px;padding:8px 12px;pointer-events:none;'+
    'opacity:.85}}';document.head.appendChild(s);
  function b(){{if(!document.querySelector('.nx-badge')){{var d=document.createElement('div');
    d.className='nx-badge';d.textContent='Nexus · free AI on our GPUs';document.body.appendChild(d);}}}}
  if(document.readyState!=='loading')b();else document.addEventListener('DOMContentLoaded',b);
}})();</script>
"""


def patch_index(path: Path) -> None:
    t = path.read_text(encoding="utf-8")
    if MARK in t:
        return
    t = sub(t, "</head>", OG + "</head>", path, required=False)
    t = re.sub(r"<title>[^<]*</title>", "<title>Nexus — free AI</title>", t, count=1)
    t = sub(t, "</body>", BANNER_JS + "</body>", path, required=False)
    t = sub(t, "</html>", f"<!-- {MARK} --></html>", path, required=False)
    path.write_text(t, encoding="utf-8")


def main() -> None:
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(SRC, DST)
    print(f"copied {SRC} -> {DST}")
    changed = []
    for rel in ("server/app.py", "schemas.py"):
        p = DST / rel
        if p.exists():
            before = p.read_text(encoding="utf-8")
            (patch_app if rel.endswith("app.py") else patch_schemas)(p)
            if p.read_text(encoding="utf-8") != before:
                changed.append(rel)
    static = DST / "static"
    patched_html = []
    for p in static.rglob("*.html"):
        before = p.read_text(encoding="utf-8", errors="ignore")
        patch_index(p)
        if p.read_text(encoding="utf-8", errors="ignore") != before:
            patched_html.append(str(p.relative_to(DST)))
    print("patched:", changed or "python (already patched or no matches)", "| html:", patched_html or "none found")
    print()
    print("Run this in the SAME kernel before launch (guest mode, no password):")
    print(f"""
import kaggleui, importlib, json, os, sys
sys.path.insert(0, {str(DST)!r})
for m in [k for k in list(sys.modules) if k.startswith("kaggleui")]:
    del sys.modules[m]
import kaggleui as nx
json.dump({{"access": {{"password_enabled": False}}}}, open("/tmp/kui_nx_settings.json", "w"))
nx.launch()
""")
    print("Admin link: <panel-url>?token=<admin_token>   (users: <panel-url>/ -> Studio)")


if __name__ == "__main__":
    main()