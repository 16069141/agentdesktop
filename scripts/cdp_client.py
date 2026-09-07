#!/usr/bin/env python3
"""CDP driver for testing the 颤翎子 Electron client (user-perspective GUI tests).

Usage: python3 cdp_client.py <subcommand> [args]

Subcommands:
  targets                       List available debug targets
  shot <out.png>                Screenshot the renderer viewport
  eval <js>                     Evaluate JS in renderer and print JSON result
  click <x> <y>                 Click at viewport coords (CSS px)
  dblclick <x> <y>              Double click
  type <js_selector> <text>     Focus element via JS, then type text
  key <js_selector> <key>       Send key to focused element (Enter etc.)
  url                           Print current location
  metrics                       Print performance metrics + timing
  text                          Print full visible text of document body
"""
import sys, json, time, base64, os
import websocket

PORT = int(os.environ.get("CDP_PORT", "9222"))
BASE = f"http://127.0.0.1:{PORT}"

def get_page_ws():
    import urllib.request
    with urllib.request.urlopen(f"{BASE}/json", timeout=5) as r:
        targets = json.load(r)
    for t in targets:
        if t.get("type") == "page":
            return t["webSocketDebuggerUrl"], t
    raise RuntimeError("no page target found: " + json.dumps(targets, ensure_ascii=False)[:500])

class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=90)
        self.id = 0
    def call(self, method, params=None):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
    def close(self):
        self.ws.close()

def main():
    cmd = sys.argv[1]
    url, target = get_page_ws()
    c = CDP(url)
    if cmd == "targets":
        print(json.dumps(target, ensure_ascii=False, indent=1))
    elif cmd == "url":
        print(c.call("Page.getNavigationHistory")["currentEntry"]["url"])
    elif cmd == "shot":
        c.call("Page.enable")
        try:
            r = c.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
            data = r["data"]
        except Exception as e:
            # fallback: canvas-based capture
            r = c.call("Runtime.evaluate", {"expression": "(()=>{const c=document.createElement('canvas'); c.width=document.documentElement.clientWidth; c.height=document.documentElement.clientHeight; const ctx=c.getContext('2d'); ctx.drawWindow? ctx.drawWindow(window,0,0,c.width,c.height,'rgb(255,255,255)') : ctx.fillStyle='#fff'; ctx.fillRect(0,0,c.width,c.height); return c.toDataURL('image/png').split(',')[1];})()", "returnByValue": True})
            data = r["result"]["value"]
        with open(sys.argv[2], "wb") as f:
            f.write(base64.b64decode(data))
        print("saved", sys.argv[2])
    elif cmd == "eval":
        expr = sys.argv[2]
        r = c.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r:
            print("EXCEPTION:", json.dumps(r["exceptionDetails"], ensure_ascii=False)[:800])
        else:
            val = r.get("result", {}).get("value")
            print(json.dumps(val, ensure_ascii=False, default=str))
    elif cmd == "click":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        for etype in ("mousePressed", "mouseReleased"):
            c.call("Input.dispatchMouseEvent", {"type": etype, "x": x, "y": y, "button": "left", "clickCount": 1})
        print("clicked", x, y)
    elif cmd == "dblclick":
        x, y = int(sys.argv[2]), int(sys.argv[3])
        for etype in ("mousePressed", "mouseReleased"):
            c.call("Input.dispatchMouseEvent", {"type": etype, "x": x, "y": y, "button": "left", "clickCount": 2})
        print("dblclicked", x, y)
    elif cmd == "type":
        sel, text = sys.argv[2], sys.argv[3]
        c.call("Runtime.evaluate", {"expression": f"(()=>{{const e=document.querySelector({json.dumps(sel)}); if(!e) return 'no-el'; e.focus(); return 'ok';}})()", "returnByValue": True})
        # Insert text via Input.insertText (native IME path)
        c.call("Input.insertText", {"text": text})
        print("typed into", sel)
    elif cmd == "key":
        sel, key = sys.argv[2], sys.argv[3]
        c.call("Runtime.evaluate", {"expression": f"(()=>{{const e=document.querySelector({json.dumps(sel)}); if(!e) return 'no-el'; e.focus(); return 'ok';}})()", "returnByValue": True})
        c.call("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "code": key, "windowsVirtualKeyCode": 13 if key == "Enter" else 0})
        c.call("Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": key, "windowsVirtualKeyCode": 13 if key == "Enter" else 0})
        print("key", key)
    elif cmd == "metrics":
        c.call("Performance.enable")
        m = c.call("Performance.getMetrics")["metrics"]
        d = {x["name"]: x["value"] for x in m}
        timing = c.call("Runtime.evaluate", {"expression": "JSON.stringify(performance.timing ? {navStart: performance.timing.navigationStart, domContentLoaded: performance.timing.domContentLoadedEventEnd, load: performance.timing.loadEventEnd, now: Date.now()} : null)", "returnByValue": True})["result"]["value"]
        d["_timing"] = timing
        print(json.dumps(d))
    elif cmd == "text":
        r = c.call("Runtime.evaluate", {"expression": "document.body.innerText", "returnByValue": True})
        print(r.get("result", {}).get("value", ""))
    c.close()

if __name__ == "__main__":
    main()
