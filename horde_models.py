"""Fetch AI Horde text models and pick best. No key needed for listing.
Usage: python3 horde_models.py
Env HORDE_API_KEY optional, defaults to 0000000000 (anonymous, slow).
Scores by performance with queue penalty, so best = fast + actually available.
"""
import json, os, urllib.request

URL = "https://stablehorde.net/api/v2/status/models?type=text"

def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "nexus-free-ai/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def score(m):
    perf = float(m.get("performance") or 0)
    eta = float(m.get("eta") or 0)
    count = int(m.get("count") or 0)
    if count <= 0 or perf <= 0:
        return -1
    # penalize queue: eta seconds to clear
    return perf / (1 + eta / 30.0)

def pick(models, top=8):
    ranked = sorted(models, key=score, reverse=True)
    return [m for m in ranked if score(m) > 0][:top]

if __name__ == "__main__":
    models = fetch()
    best = pick(models, 10)
    print(f"fetched {len(models)} text models")
    print(f"{'score':>7} {'tps':>6} {'eta':>5} {'wrk':>3}  name")
    for m in best:
        print(f"{score(m):7.2f} {m['performance']:6.1f} {m['eta']:5.0f} {m['count']:3d}  {m['name']}")
    if best:
        print("\nBEST=" + best[0]["name"])
