#!/usr/bin/env python3
"""Send a chat message via UI and measure streaming latency.
Usage: python3 chat_latency.py "<text>" <shot_path>
Measures: send->first char, send->stable completion, plus samples of growing text.
"""
import sys, json, time, subprocess, os, base64

BASE = os.path.dirname(os.path.abspath(__file__))
def cdp(*args):
    return subprocess.run([sys.executable, os.path.join(BASE, "cdp_client.py"), *args],
                          capture_output=True, text=True).stdout.strip()

def evaljs(expr):
    out = cdp("eval", expr)
    if out.startswith("EXCEPTION"):
        return {"__exc__": out}
    try:
        return json.loads(out)
    except Exception:
        return out

def shots(name):
    cdp("shot", os.path.join(os.path.dirname(BASE), "test-report", "shots", name))

text = sys.argv[1]

# pre-state: last message count
before = evaljs("document.querySelectorAll('[class*=message], [class*=Message]').length")
# mark time, click send
evaljs("window.__t0=performance.now(); 'ok'")
sent = evaljs("(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='发送' && x.offsetParent!==null); if(!b) return 'no-send-btn'; b.click(); return 'clicked';})()")
t_send = time.time()
print("send:", sent)

first_text = None
first_t = None
stable_at = None
stable_text = ""
last_len = -1
unchanged = 0
rows = []
for i in range(120):  # up to 120s
    time.sleep(0.25)
    info = evaljs("(()=>{const msgs=[...document.querySelectorAll('div')].filter(d=>/user|assistant|bot|message/i.test(d.className||'')); "
                  "const txt=document.body.innerText; return JSON.stringify({hasUser: txt.includes(%s), nMsgs: msgs.length, tail: txt.slice(-200)});})()" % json.dumps(text[:8]))
    body = evaljs("document.body.innerText")
    # assistant text = body minus known UI chrome; track growth of last assistant block
    if first_text is None and body and ("介绍" in body and "类事情" in body):
        # detect if assistant has started replying: look for marker of reply
        pass
    # heuristic: find index of the user's message then what follows
    if first_t is None:
        # look for the response container: messages area grows with a new assistant bubble
        n = evaljs("document.querySelectorAll('[class*=message],[class*=Message],[class*=bubble],[class*=Bubble]').length")
        if isinstance(n, (int, float)) and n > (before if isinstance(before,(int,float)) else 0):
            first_t = time.time() - t_send
            first_text = True
    # stability: body text length
    blen = len(body) if isinstance(body, str) else 0
    rows.append((round(time.time()-t_send,2), blen))
    if blen == last_len:
        unchanged += 1
        if unchanged >= 4 and last_len > 100:
            stable_at = time.time() - t_send
            stable_text = body[-1500:]
            break
    else:
        unchanged = 0
        last_len = blen

print("first_t(s):", first_t)
print("stable_at(s):", stable_at)
print("final body len:", last_len)
print("growth:", json.dumps(rows[::8]))
with open(os.path.join(os.path.dirname(BASE), "test-report", "chat_latency.json"), "w") as f:
    json.dump({"first_t": first_t, "stable_at": stable_at, "final_len": last_len, "growth": rows}, f, ensure_ascii=False, indent=1)
shots("02-chat-reply.png")
print("stable tail:", stable_text[-300:])
