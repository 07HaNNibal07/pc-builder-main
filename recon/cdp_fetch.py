import json, subprocess, sys, time, urllib.request
import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9222
url = sys.argv[1]
out = sys.argv[2]
wait = float(sys.argv[3]) if len(sys.argv) > 3 else 12

proc = subprocess.Popen([
    CHROME,
    f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
    r"--user-data-dir=C:\Temp\chr-cdp",
    "--no-first-run", "--no-default-browser-check",
    "--window-size=1600,1000", "--lang=ru-RU",
    "about:blank",
])

# ждём отладочный порт
ws_url = None
for _ in range(40):
    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
        pages = [t for t in tabs if t.get("type") == "page"]
        if pages:
            ws_url = pages[0]["webSocketDebuggerUrl"]
            break
    except Exception:
        pass
    time.sleep(0.5)

if not ws_url:
    print("НЕ УДАЛОСЬ подключиться к Chrome")
    proc.terminate(); sys.exit(1)

ws = websocket.create_connection(ws_url, timeout=90)
def cmd(i, method, params=None):
    ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == i:
            return m

cmd(1, "Page.enable")
cmd(2, "Page.navigate", {"url": url})
time.sleep(wait)
r = cmd(3, "Runtime.evaluate", {
    "expression": "document.documentElement.outerHTML",
    "returnByValue": True,
})
html = r.get("result", {}).get("result", {}).get("value", "")
open(out, "w", encoding="utf-8").write(html)
print(f"получено {len(html)} байт -> {out}")
ws.close()
proc.terminate()
