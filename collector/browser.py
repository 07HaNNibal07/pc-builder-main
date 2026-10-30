"""Управление настоящим браузером Chrome по протоколу CDP.

Часть площадок (DNS, Ozon, Яндекс Маркет) закрыта защитой от ботов,
которая отличает программу от браузера и не пропускает даже Chrome в
безоконном режиме. Настоящий видимый Chrome, управляемый по протоколу
отладки, она пропускает. Модуль открывает такой браузер, загружает
страницы и выполняет в них JavaScript.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

import websocket

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9222
# Отдельный профиль: не мешает обычному браузеру пользователя и хранит
# cookie площадок между запусками
PROFILE = r"C:\Temp\chr-cdp"


class BrowserError(Exception):
    """Браузер не запустился или не ответил."""


class Browser:
    def __init__(self, port: int = PORT, profile: str = PROFILE):
        self.port = port
        self.profile = profile
        self.proc: subprocess.Popen | None = None
        self.ws: websocket.WebSocket | None = None
        self._next_id = 0

    def open(self) -> None:
        if self.ws is not None:
            return
        if not self._tabs():
            self.proc = subprocess.Popen([
                CHROME, f"--remote-debugging-port={self.port}",
                # Без этого флага websocket-подключение отклоняется с 403
                "--remote-allow-origins=*",
                f"--user-data-dir={self.profile}", "--no-first-run",
                "--no-default-browser-check", "--window-size=1600,1000",
                "--lang=ru-RU", "about:blank",
            ])
        for _ in range(40):
            pages = [t for t in self._tabs() if t.get("type") == "page"]
            if pages:
                self.ws = websocket.create_connection(
                    pages[0]["webSocketDebuggerUrl"], timeout=60)
                return
            time.sleep(0.5)
        self.close()
        raise BrowserError("Chrome не открыл порт отладки")

    def _tabs(self) -> list[dict]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json", timeout=2) as r:
                return json.load(r)
        except OSError:
            return []

    def call(self, method: str, params: dict | None = None) -> dict:
        self.open()
        self._next_id += 1
        call_id = self._next_id
        self.ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == call_id:
                if "error" in message:
                    raise BrowserError(f"{method}: {message['error']}")
                return message.get("result", {})

    def evaluate(self, expression: str):
        """Выполнить JavaScript в странице и вернуть значение."""
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            raise BrowserError(f"ошибка скрипта: {result['exceptionDetails'].get('text')}")
        return result.get("result", {}).get("value")

    def navigate(self, url: str, ready: str | None = None, timeout: float = 30) -> bool:
        """Открыть страницу и дождаться её готовности.

        ``ready`` — выражение JavaScript, истинное, когда нужные данные
        отрисованы. Возвращает False, если их не дождались: пустая страница
        выдачи — законный исход, решение принимает вызывающий код.
        """
        self.call("Page.navigate", {"url": url})
        # Сначала уйти со старой страницы, иначе проверка сработает на ней
        time.sleep(1.5)
        condition = f"document.readyState === 'complete' && ({ready})" if ready \
            else "document.readyState === 'complete'"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.evaluate(condition):
                    return True
            except BrowserError:
                pass  # страница ещё грузится, контекст скриптов пересоздаётся
            time.sleep(1)
        return False

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            except OSError:
                pass
            self.ws = None
        if self.proc is not None:
            self.proc.terminate()
            self.proc = None
