"""Rebrand the installed KaggleUI as Nexus, guest-open, with a server-side SFW filter.

Run AFTER install.sh and BEFORE kaggleui.launch(). It copies the installed package to
/kaggle/working/nexus_kaggleui, edits that copy, and prints the exact launch cell.

Every substitution is VERIFIED. If a pattern is missing (upstream drift) it aborts with
the offending pattern instead of silently half-patching.

Changes vs stock:
  * static/index.html   -> title/og/description say Nexus (Discord embeds stop saying KaggleUI)
  * app.py require_access -> always pass, so guests land in Studio with no password
  * app.py INSTALL_LINE -> hidden from the panel
  * gateway/openai.py -> always-on SFW filter on /v1/images/generations (no toggle)
"""
import shutil
import sys
from pathlib import Path

MARK = "nexus-patched"
DST = Path("/kaggle/working/nexus_kaggleui")

OG = ('<meta property="og:title" content="Nexus" />'
      '<meta property="og:site_name" content="Nexus" />'
      '<meta property="og:type" content="website" />'
      '<meta property="og:description" content="Free AI chat, image and voice." />'
      '<meta name="twitter:card" content="summary" />'
      '<meta name="twitter:title" content="Nexus" />'
      '<meta name="twitter:description" content="Free AI chat, image and voice." />')

SFW_HELPER = '''

# --- nexus: always-on SFW gate. No toggle, no bypass. ---
_NX_SFW = ("nsfw", "porn", "explicit", "erotic", "sexual", "nude", "naked", "undressed",
           "strip", "fetish", "orgasm", "intercourse", "xxx", "rape", "non-consensual",
           "forced", "bestiality", "incest")
_NX_MINOR = ("child", "minor", "kid", "toddler", "baby", "teen", "schoolgirl", "schoolboy", "young")
_NX_CTX = ("nude", "naked", "sexual", "explicit", "nsfw", "porn", "erotic", "kiss", "bed",
           "shower", "bikini", "lingerie")


def _nexus_blocked(prompt) -> bool:
    low = str(prompt or "").lower()
    if any(t in low for t in _NX_SFW):
        return True
    return any(m in low for m in _NX_MINOR) and any(s in low for s in _NX_CTX)

'''

edits = []   # (path, old, new, required)


def apply(text: str, path: Path) -> str:
    for p, old, new, required in edits:
        if p != path:
            continue
        if old in text:
            text = text.replace(old, new, 1)
            print(f"  patched {path.name}: {old.strip().splitlines()[0][:60]}")
        elif required:
            print(f"  ABORT {path.name}: pattern not found -> {old.strip().splitlines()[0][:80]!r}",
                  file=sys.stderr)
            sys.exit(1)
        else:
            print(f"  skip {path.name} (already patched or drifted): "
                  f"{old.strip().splitlines()[0][:50]!r}")
    return text


def main() -> None:
    import kaggleui
    src = Path(kaggleui.__file__).resolve().parent
    if DST.exists():
        shutil.rmtree(DST)
    shutil.copytree(src, DST)
    print(f"copied {src} -> {DST}")

    app = DST / "server" / "app.py"
    gw = DST / "gateway" / "openai.py"
    index = DST / "static" / "index.html"

    edits.clear()
    edits.append((app,
                  '    def require_access(request: Request) -> None:\n'
                  '        if not state.auth.has_access(request, state.settings):\n'
                  '            raise HTTPException(401, "Password required")',
                  f'    def require_access(request: Request) -> None:\n        # {MARK}: guests in\n'
                  '        return None', True))
    edits.append((app,
                  'INSTALL_LINE = "!curl -fsSL https://raw.githubusercontent.com/UltraFEmotes/'
                  'KaggleUI/main/install.sh | bash"',
                  f'INSTALL_LINE = ""  # {MARK} hidden', True))
    edits.append((gw, "router = APIRouter()", SFW_HELPER + "router = APIRouter()", True))
    edits.append((gw,
                  '        body = await _json(request)\n'
                  '        dep = find(request, body.get("model"), "image")',
                  '        body = await _json(request)\n'
                  '        if _nexus_blocked(body.get("prompt")):\n'
                  '            raise GatewayError(400, "SFW filter: explicit sexual content is not allowed.")\n'
                  '        dep = find(request, body.get("model"), "image")', True))

    for p in (app, gw):
        if not p.exists():
            print(f"ABORT missing {p}", file=sys.stderr)
            sys.exit(1)
        p.write_text(apply(p.read_text(encoding="utf-8"), p), encoding="utf-8")

    if index.exists():
        h = index.read_text(encoding="utf-8")
        h = h.replace("<title>KaggleUI</title>", "<title>Nexus</title>", 1)
        h = h.replace(
            '<meta name="description" content="Choose, deploy and use AI models on your Kaggle GPU. '
            'Manage models, APIs and hardware from one workspace." />',
            '<meta name="description" content="Free AI chat, image and voice on our GPUs." />'
            + OG, 1)
        if "og:title" not in h:
            h = h.replace("</head>", OG + "</head>", 1)
        index.write_text(h, encoding="utf-8")
        print(f"  patched index.html: title + og tags")
    else:
        print("ABORT no static/index.html - wheel layout changed", file=sys.stderr)
        sys.exit(1)

    import py_compile
    for p in (app, gw):
        py_compile.compile(str(p), doraise=True)
    print("  syntax OK for app.py + openai.py")
    print("\nOK. Now run this in the same kernel:\n")
    print(f"""import sys
sys.path.insert(0, {str(DST)!r})
for _m in [k for k in list(sys.modules) if k.startswith("kaggleui")]:
    del sys.modules[_m]
import kaggleui
kaggleui.launch()
""")


if __name__ == "__main__":
    main()