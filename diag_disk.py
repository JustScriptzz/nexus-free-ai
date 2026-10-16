"""Read-only disk + import diagnostics. Writes nothing, deletes nothing."""
import json
import os
import subprocess
import urllib.request

HOME = os.environ.get("KAGGLEUI_HOME", "/tmp/kaggleui")
ST = json.load(open(f"{HOME}/state.json"))
H = {"Authorization": f"Bearer {ST['admin_token']}"}


def sh(cmd):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    return (p.stdout or p.stderr).strip()


print("== disk ==")
print(sh("df -h /tmp /kaggle/working / 2>/dev/null | sed 's/  */ /g'"))

print("\n== biggest dirs under KAGGLEUI_HOME ==")
print(sh(f"du -sh {HOME}/* 2>/dev/null | sort -rh | head -8"))

print("\n== import job dirs (orphans count as disk) ==")
print(sh(f"ls -1 {HOME}/imports 2>/dev/null | head -20"))
print(sh(f"du -sh {HOME}/imports/* 2>/dev/null | sort -rh | head -6"))

print("\n== live imports ==")
r = urllib.request.Request(
    f"http://127.0.0.1:{ST['port']}/_kui/api/catalog/imports", headers=H)
d = json.loads(urllib.request.urlopen(r, timeout=30).read())
jobs = d if isinstance(d, list) else (d.get("imports") or d.get("jobs") or d.get("data") or [])
for j in jobs:
    print(f"  {j.get('id')} {j.get('status')} "
          f"{round((j.get('bytes') or 0) / 1e9, 2)}GB {str(j.get('url'))[-60:]}")

print("\n== ollama store (where the 14B already sits) ==")
print(sh("du -sh /tmp/kaggleui/models/ollama 2>/dev/null; du -sh /root/.ollama 2>/dev/null"))