"""Адаптер Wildberries: публичный поисковый интерфейс витрины.

Ключ не нужен, но без заголовков браузера WB отвечает 429 даже на первый
запрос. При частых запросах он ограничивает частоту «приступами»: серия
отказов, затем снова пропускает. Поэтому запросы идут не чаще одного в
MIN_INTERVAL секунд, а на 429 адаптер ждёт с нарастающей паузой.
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Callable

import requests

from collector.adapters.base import Adapter, OfferData, Page, SourceError

# v4 не работает, v13 и v18 отвечают 429 даже с заголовками браузера
SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"),
    "Accept": "*/*",
    "Origin": "https://www.wildberries.ru",
    "Referer": "https://www.wildberries.ru/",
}
# Регион доставки — Москва: от него зависят цена и наличие
DEST = "-1257786"

MIN_INTERVAL = 8.0
# Паузы после 429, секунды. Проверка 10.09.2026: после серии отказов WB
# пропускал запрос через 20–60 с, но однажды не пропустил и за две минуты.
BACKOFF = (20, 40, 60, 90, 120)


class WildberriesAdapter(Adapter):
    code = "wb"
    page_size = 100

    def __init__(self, min_interval: float = MIN_INTERVAL,
                 log: Callable[[str], None] = print):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.min_interval = min_interval
        self.log = log
        self._last_request = 0.0
        self.requests_made = 0
        self.rate_limited = 0

    def search(self, query: str, page: int) -> Page:
        data = self._get({
            "appType": 1, "curr": "rub", "dest": DEST, "lang": "ru",
            "query": query, "resultset": "catalog", "sort": "popular",
            "limit": self.page_size, "page": page,
        })
        products = data.get("products") or data.get("data", {}).get("products") or []
        offers, unpriced = [], 0
        for item in products:
            offer = self._parse(item)
            if offer is None:
                unpriced += 1
            else:
                offers.append(offer)
        return Page(offers, unpriced, last=len(products) < self.page_size)

    def _get(self, params: dict) -> dict:
        reason = ""
        for attempt, pause in enumerate((0, *BACKOFF), start=1):
            if pause:
                self.log(f"    {reason} — пауза {pause} с, попытка {attempt}")
                time.sleep(pause)
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.get(SEARCH_URL, params=params, timeout=30)
            except (requests.Timeout, requests.ConnectionError) as exc:
                # Обрыв и таймаут — такие же временные отказы, как 429:
                # 10.09.2026 WB однажды не ответил за 30 с посреди сбора
                reason = f"сеть: {type(exc).__name__}"
                continue
            except requests.RequestException as exc:
                raise SourceError(f"сеть: {exc}") from exc
            finally:
                self._last_request = time.monotonic()
                self.requests_made += 1

            if response.status_code == 429:
                self.rate_limited += 1
                reason = "429"
                continue
            if response.status_code != 200:
                raise SourceError(f"HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                raise SourceError("ответ не является JSON") from exc
        raise SourceError(f"{reason}: {len(BACKOFF) + 1} отказов подряд")

    @staticmethod
    def _parse(item: dict) -> OfferData | None:
        # Цена хранится у варианта товара; у комплектующих вариант обычно один.
        # Вариант без цены означает, что купить товар сейчас нельзя.
        prices = [s["price"] for s in item.get("sizes", [])
                  if (s.get("price") or {}).get("product")]
        if not prices:
            return None
        price = min(prices, key=lambda p: p["product"])
        nm = item["id"]
        return OfferData(
            external_id=str(nm),
            category_ref=str(item.get("subjectId")),
            source_name=(item.get("name") or "").strip(),
            url=f"https://www.wildberries.ru/catalog/{nm}/detail.aspx",
            # Цены приходят в копейках
            price=Decimal(price["product"]) / 100,
            price_basic=Decimal(price["basic"]) / 100 if price.get("basic") else None,
            quantity=item.get("totalQuantity"),
            brand=item.get("brand") or None,
            seller=item.get("supplier") or None,
            rating=item.get("reviewRating") or item.get("rating"),
            feedbacks=item.get("feedbacks"),
            raw=item,
        )
