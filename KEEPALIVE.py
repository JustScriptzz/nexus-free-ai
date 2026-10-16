"""Keep the KaggleUI session alive and heal its tunnel.

Run in its own cell AFTER launch():

    !cd /kaggle/working/free-ai-website && python3 KEEPALIVE.py

What it does:
  * heartbeat - hits the local server every 45s so Kaggle never sees an idle kernel
  * tunnel watchdog - if the public tunnel drops, calls the restart endpoint so the
    link comes back instead of dying silently
  * model watchdog - reports deployments that fall out of `ready`

What it CANNOT do: beat Kaggle's hard session cap (~12h) or the weekly GPU quota
(~30h). When the session is killed, this dies with it. Pair it with a Kaggle
notification secret so you get pinged the moment that happens.
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HOME = os.environ.get("KAGGLEUI_HOME", "/tmp/kaggleui")
STATE = os.path.join(HOME, "state.json")
HEARTBEAT = 45
WATCHDOG = 180
stop = threading.Event()


def log(msg):
    print(f"[keepalive {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def get(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def post(url, headers=None, timeout=30):
    req = urllib.request.Request(url, data=b"{}", method="POST", headers=headers or {})
    return urllib.request.urlopen(req, timeout=timeout)


def heartbeat():
    """Cheap local call. Keeps the kernel visibly busy so idle-kill doesn't fire."""
    while not stop.is_set():
        st = state()
        port = st.get("port")
        if port:
            url = f"http://127.0.0.1:{port}/_kui/api/session"
            try:
                get(url, {"Authorization": f"Bearer {st.get('admin_token', '')}"})
                log(f"heartbeat ok (status={st.get('status')})")
            except urllib.error.HTTPError as e:
                log(f"heartbeat HTTP {e.code}")
            except Exception as e:
                log(f"heartbeat failed: {type(e).__name__}")
        stop.wait(HEARTBEAT)


def watchdog():
    """Heal a dead tunnel and report model health."""
    last_url, last_report = None, 0.0
    while not stop.is_set():
        st = state()
        port, url = st.get("port"), st.get("url")
        if port and st.get("status") == "running":
            # tunnel dead but server up -> restart it
            if url and url != last_url:
                log(f"public URL: {url.split('?')[0]}")
                last_url = url
            if url:
                try:
                    get(url.split("?")[0] + "/_kui/api/session", timeout=20)
                except Exception:
                    log("tunnel unreachable, restarting...")
                    try:
                        post(f"http://127.0.0.1:{port}/_kui/api/tunnels/main/restart",
                             {"Authorization": f"Bearer {st.get('admin_token', '')}"})
                        log("tunnel restart requested")
                    except Exception as e:
                        log(f"tunnel restart failed: {e}")
                    last_url = None
        elif st.get("status") == "tunnel_error":
            log("tunnel_error state, retrying restart")
            try:
                post(f"http://127.0.0.1:{port}/_kui/api/tunnels/main/restart",
                     {"Authorization": f"Bearer {st.get('admin_token', '')}"})
            except Exception:
                pass

        if time.time() - last_report > 600:
            last_report = time.time()
            try:
                deps = json.loads(get(
                    f"http://127.0.0.1:{port}/_kui/api/deployments",
                    {"Authorization": f"Bearer {st.get('admin_token', '')}"}).read())
                rows = deps if isinstance(deps, list) else deps.get("deployments", [])
                summary = ", ".join(f"{d.get('name')}={d.get('status')}" for d in rows)
                log(f"models: {summary or 'none'}")
            except Exception:
                pass
        stop.wait(WATCHDOG)


if __name__ == "__main__":
    st = state()
    if not st:
        print("No state.json yet - run kaggleui.launch() first.")
        sys.exit(1)
    log(f"session status={st.get('status')} url={str(st.get('url') or '').split('?')[0]}")
    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        watchdog()
    except KeyboardInterrupt:
        stop.set()
        log("stopped")