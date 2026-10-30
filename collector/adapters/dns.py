"""Адаптер DNS: список товаров — через браузер, цены — напрямую.

Защита DNS (Qrator) пропускает к страницам каталога только настоящий
браузер, поэтому раздел каталога открывается в Chrome. Со страницы
берутся коды товаров и ключ запроса цен (hash). Цены по кодам страница
получает отдельным запросом product-buy — он защитой не закрыт и
выполняется напрямую, без браузера.

Ключ к списку товаров не привязан, но живёт ограниченное время: через
~40 минут с ним приходит пустой ответ (проверено 11.09.2026). Поэтому он
берётся заново с каждой загруженной страницы.

Наименования DNS единообразны и содержат артикул производителя в
квадратных скобках — «Видеокарта Palit GeForce RTX 5070 Infinity 3
[NE75070019K9-GB2050S]». Поэтому DNS служит справочником моделей.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Callable
from urllib.parse import urljoin

import requests

from collector.adapters.base import Adapter, OfferData, Page, SourceError
from collector.browser import Browser, BrowserError

BASE = "https://www.dns-shop.ru"
BUY_URL = BASE + "/ajax-state/product-buy/"
PAGE_SIZE = 24
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"),
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE,
    "Referer": BASE + "/",
}
# Пауза между страницами каталога: браузерный сбор не должен выглядеть
# как перебор
MIN_INTERVAL = 4.0

CARDS_READY = "document.querySelectorAll('div.catalog-product[data-code]').length > 0"

# Извлечение карточек выполняется в самой странице: разбор DOM надёжнее
# регулярных выражений по HTML
EXTRACT = r"""
(() => {
  const html = document.documentElement.outerHTML;
  const hash = (html.match(/"type":"product-buy","hash":"([0-9a-f]+)"/) || [])[1] || null;
  const cards = [...document.querySelectorAll('div.catalog-product[data-code]')].map(el => ({
    code: el.dataset.code,
    guid: el.dataset.product || null,
    avail: el.dataset.availStatus || null,
    href: el.querySelector('a[href*="/product/"]')?.getAttribute('href') || null,
    name: el.querySelector('img[alt]')?.getAttribute('alt') || null,
  }));
  return JSON.stringify({hash, cards, title: document.title});
})()
"""


class DnsAdapter(Adapter):
    code = "dns"
    page_size = PAGE_SIZE

    def __init__(self, log: Callable[[str], None] = print):
        self.browser = Browser()
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.log = log
        self._last_page = 0.0
        self.requests_made = 0
        self.rate_limited = 0

    def search(self, query: str, page: int) -> Page:
        """Страница раздела каталога; ``query`` — путь раздела."""
        wait = MIN_INTERVAL - (time.monotonic() - self._last_page)
        if wait > 0:
            time.sleep(wait)
        try:
            ready = self.browser.navigate(f"{BASE}{query}?p={page}", CARDS_READY)
            data = json.loads(self.browser.evaluate(EXTRACT))
        except (BrowserError, OSError, ValueError) as exc:
            raise SourceError(f"браузер: {exc}") from exc
        finally:
            self._last_page = time.monotonic()
            self.requests_made += 1

        cards = data["cards"]
        if not cards:
            if not ready and "DNS" not in data["title"]:
                raise SourceError(f"страница каталога не отдана: «{data['title'][:60]}»")
            # Пустая первая страница — не конец выдачи, а ошибка настройки:
            # раздел содержит подразделы, а не товары (так было с памятью
            # и SSD 11.09.2026). Молча записать ноль нельзя.
            if page == 1:
                raise SourceError(f"в разделе нет товаров — возможно, в нём только"
                                  f" подразделы: «{data['title'][:60]}»")
            return Page([], last=True)
        if not data["hash"]:
            raise SourceError("на странице нет ключа запроса цен")

        states = self._prices(data["hash"], [c["code"] for c in cards])
        # Раздел каталога однозначно задаёт категорию
        category_ref = query.strip("/").split("/")[1]
        offers, unpriced = [], 0
        for card in cards:
            state = states.get(card["code"]) or {}
            current = (state.get("price") or {}).get("current")
            if not current:
                unpriced += 1
                continue
            offers.append(OfferData(
                external_id=card["code"],
                category_ref=category_ref,
                source_name=(state.get("name") or card["name"] or "").strip(),
                # href бывает и полным адресом — склейка давала «https://www.dns-shop.ruhttps://…»
                url=urljoin(BASE, card["href"]) if card["href"] else f"{BASE}{query}",
                price=Decimal(str(current)),
                # Количество DNS не раскрывает, только срок получения
                available=card["avail"] != "out_of_stock",
                seller="DNS",
                raw={"card": card, "state": state},
            ))
        return Page(offers, unpriced, last=len(cards) < PAGE_SIZE)

    def _prices(self, hash_: str, codes: list[str]) -> dict[str, dict]:
        """Цены и наименования по кодам товаров: код → данные товара."""
        payload = {
            "type": "product-buy", "hash": hash_,
            # Идентификатор контейнера возвращается в ответе: по нему
            # ответ сопоставляется с кодом товара
            "containers": [{"id": f"c{code}", "data": {"id": code}} for code in codes],
        }
        for attempt, pause in enumerate((0, 5, 15, 30), start=1):
            if pause:
                self.log(f"    цены DNS: повтор через {pause} с, попытка {attempt}")
                time.sleep(pause)
            try:
                response = self.session.post(
                    BUY_URL, data={"data": json.dumps(payload)}, timeout=30)
            except (requests.Timeout, requests.ConnectionError):
                continue
            finally:
                self.requests_made += 1
            if response.status_code == 429:
                self.rate_limited += 1
                continue
            if response.status_code != 200:
                raise SourceError(f"цены DNS: HTTP {response.status_code}")
            try:
                states = (response.json().get("data") or {}).get("states") or []
            except ValueError as exc:
                raise SourceError("цены DNS: ответ не является JSON") from exc
            return {s["id"][1:]: s.get("data") or {} for s in states}
        raise SourceError("цены DNS: четыре отказа подряд")

    def close(self) -> None:
        self.browser.close()
        self.session.close()
