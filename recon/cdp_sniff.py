"""Разведка внутренних интерфейсов площадок: какие запросы делает страница поиска.

Запуск (Windows-Python с websocket-client):
    python cdp_sniff.py [площадка ...]

Открывает настоящий Chrome через CDP, по очереди загружает страницу поиска
каждой площадки, прокручивает её — подгрузка следующих товаров идёт фоновыми
запросами — и записывает сетевой обмен. Итог: sniff_<площадка>.json (запросы
XHR/Fetch с заголовками и началом ответа), cookies_<площадка>.json и сводка
в консоль.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket

sys.stdout.reconfigure(encoding="utf-8")

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9222
HERE = os.path.dirname(os.path.abspath(__file__))

SITES = {
    "dns": "https://www.dns-shop.ru/search/?q=rtx+5070",
    "ozon": "https://www.ozon.ru/search/?text=rtx+5070&from_global=true",
    "ym": "https://market.yandex.ru/search?text=rtx%205070",
    "ali": "https://aliexpress.ru/wholesale?SearchText=rtx+5070",
}

# Счётчики, реклама, телеметрия — к данным о товарах отношения не имеют
NOISE = (
    "mc.yandex", "yandex.ru/clck", "an.yandex", "adfox", "google-analytics",
    "googletagmanager", "doubleclick", "top-fwz", "vk.com", "facebook", "criteo",
    "sentry", "tns-counter", "scorecardresearch", "webvisor", "/metrics",
    "mc.webvisor", "yastatic.net", "tracker", "/collect", "/rum",
)


class CDP:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=90)
        self.ws.settimeout(1)
        self.next_id = 0
        self.events: list[dict] = []

    def _recv(self):
        try:
            return json.loads(self.ws.recv())
        except websocket.WebSocketTimeoutException:
            return None

    def call(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        self.next_id += 1
        call_id = self.next_id
        self.ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv()
            if message is None:
                continue
            if message.get("id") == call_id:
                return message
            if "method" in message:
                self.events.append(message)
        raise TimeoutError(method)

    def pump(self, seconds: float) -> None:
        """Принимать события сети заданное время."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            message = self._recv()
            if message and "method" in message:
                self.events.append(message)


def launch() -> tuple[subprocess.Popen, CDP]:
    proc = subprocess.Popen([
        CHROME, f"--remote-debugging-port={PORT}", "--remote-allow-origins=*",
        r"--user-data-dir=C:\Temp\chr-cdp", "--no-first-run",
        "--no-default-browser-check", "--window-size=1600,1000", "--lang=ru-RU",
        "about:blank",
    ])
    for _ in range(40):
        try:
            tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
            pages = [t for t in tabs if t.get("type") == "page"]
            if pages:
                cdp = CDP(pages[0]["webSocketDebuggerUrl"])
                cdp.call("Network.enable", {"maxPostDataSize": 65536})
                cdp.call("Page.enable")
                return proc, cdp
        except Exception:
            pass
        time.sleep(0.5)
    proc.terminate()
    raise SystemExit("не удалось подключиться к Chrome")


def collect_requests(events: list[dict]) -> dict[str, dict]:
    requests: dict[str, dict] = {}
    for event in events:
        params = event["params"]
        rid = params.get("requestId")
        if event["method"] == "Network.requestWillBeSent":
            request = params["request"]
            requests.setdefault(rid, {}).update(
                url=request["url"], method=request["method"], type=params.get("type"),
                post=(request.get("postData") or "")[:2000],
                headers=request.get("headers", {}),
            )
        elif event["method"] == "Network.responseReceived":
            response = params["response"]
            entry = requests.setdefault(rid, {})
            entry.update(status=response["status"], mime=response.get("mimeType"))
            entry.setdefault("type", params.get("type"))
        elif event["method"] == "Network.loadingFinished":
            requests.setdefault(rid, {})["size"] = params.get("encodedDataLength")
    return requests


def sniff(cdp: CDP, name: str, url: str, wait: float = 12) -> None:
    cdp.events.clear()
    cdp.call("Page.navigate", {"url": url})
    cdp.pump(wait)
    # Прокрутка вызывает подгрузку следующих товаров фоновыми запросами
    for _ in range(4):
        cdp.call("Runtime.evaluate", {"expression": "window.scrollBy(0, document.body.scrollHeight)"})
        cdp.pump(2.5)

    title = cdp.call("Runtime.evaluate", {
        "expression": "document.title", "returnByValue": True,
    }).get("result", {}).get("result", {}).get("value", "")
    requests = collect_requests(cdp.events)

    document = next((r for r in requests.values() if r.get("type") == "Document"
                     and r.get("url", "").startswith(url.split("?")[0])), {})
    interesting = {
        rid: r for rid, r in requests.items()
        if r.get("type") in ("XHR", "Fetch") and r.get("url")
        and not any(n in r["url"] for n in NOISE)
    }
    for rid, entry in interesting.items():
        if "json" not in (entry.get("mime") or ""):
            continue
        try:
            body = cdp.call("Network.getResponseBody", {"requestId": rid}, timeout=10)
        except TimeoutError:
            continue
        text = body.get("result", {}).get("body", "")
        entry["body_len"] = len(text)
        entry["body_head"] = text[:600]
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        # Ответ бывает и простым значением: true, число, строка
        if isinstance(parsed, dict):
            entry["keys"] = list(parsed)[:20]
        elif isinstance(parsed, list):
            entry["keys"] = f"list[{len(parsed)}]"

    cookies = cdp.call("Network.getCookies", {"urls": [url]}).get("result", {}).get("cookies", [])
    with open(os.path.join(HERE, f"sniff_{name}.json"), "w", encoding="utf-8") as f:
        json.dump({"url": url, "title": title, "document": document,
                   "requests": list(interesting.values())}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(HERE, f"cookies_{name}.json"), "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=1)

    print(f"\n=== {name}: «{title[:70]}» — документ {document.get('status')}, "
          f"всего запросов {len(requests)}, XHR/Fetch {len(interesting)}, cookie {len(cookies)}")
    ranked = sorted(interesting.values(), key=lambda r: -(r.get("body_len") or r.get("size") or 0))
    for entry in ranked[:25]:
        size = entry.get("body_len") or entry.get("size") or 0
        keys = entry.get("keys")
        print(f"  {entry.get('status')} {entry.get('method')} {(entry.get('mime') or '')[:16]:16}"
              f" {size:>8}  {entry['url'][:150]}")
        if keys:
            print(f"        ключи: {keys}")


def main() -> None:
    names = sys.argv[1:] or list(SITES)
    proc, cdp = launch()
    try:
        for name in names:
            try:
                sniff(cdp, name, SITES[name])
            except Exception as exc:
                print(f"\n=== {name}: ошибка {type(exc).__name__}: {exc}")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
