"""Адаптер Яндекс Маркета: страница поиска в браузере, данные — из разметки.

Отдельного интерфейса с товарами у Маркета нет: выдача приходит готовым
HTML, где названия и цены лежат в разных концах документа. Поэтому
страница открывается в Chrome и разбирается уже отрисованной. У каждой
карточки есть атрибут data-zone-data с JSON: идентификатор модели Маркета
(modelId), категория (hid), магазин и цены.

Цен у карточки несколько. Крупно на витрине показана цена с картой
Яндекс Банка (yaBank) — она доступна не всем покупателям. Для сравнения
берётся цена со скидкой для всех (withDiscount), при её отсутствии — price.
Банковская цена сохраняется в raw.

Прокрутка выдачу не догружает, листание — параметром page в адресе.
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import Callable
from urllib.parse import quote

from collector.adapters.base import Adapter, OfferData, Page, SourceError
from collector.browser import Browser, BrowserError

BASE = "https://market.yandex.ru"
MIN_INTERVAL = 4.0

READY = ("document.querySelectorAll('[data-zone-name=\"productSnippet\"]').length > 0"
         " || location.pathname.includes('showcaptcha')")

EXTRACT = r"""
(() => {
  if (location.pathname.includes('showcaptcha')) return JSON.stringify({captcha: true});
  const cards = [...document.querySelectorAll('[data-zone-name="productSnippet"]')].map(s => {
    let zone = {};
    try { zone = JSON.parse(s.getAttribute('data-zone-data') || '{}') } catch (e) {}
    return {
      zone,
      title: s.querySelector('[data-auto="snippet-title"]')?.innerText || null,
      shown: (s.querySelector('[data-auto="snippet-price-current"]')?.innerText || '').replace(/\s+/g, ' '),
      href: s.querySelector('[data-auto="snippet-link"]')?.getAttribute('href') || null,
    };
  });
  return JSON.stringify({captcha: false, cards, title: document.title});
})()
"""


def _price(zone: dict) -> tuple[Decimal | None, Decimal | None]:
    """Цена для всех покупателей и цена без скидки."""
    extra = {p.get("priceType"): p.get("priceValue") for p in zone.get("additionalPrices") or []}
    base = Decimal(str(zone["price"])) if zone.get("price") else None
    discounted = Decimal(str(extra["withDiscount"])) if extra.get("withDiscount") else None
    if discounted is not None:
        return discounted, base if base and base > discounted else None
    return base, None


class YandexMarketAdapter(Adapter):
    code = "ym"
    page_size = 16

    def __init__(self, log: Callable[[str], None] = print):
        self.browser = Browser()
        self.log = log
        self._last = 0.0
        self.requests_made = 0
        self.rate_limited = 0

    def search(self, query: str, page: int) -> Page:
        """Страница выдачи; ``query`` — поисковая фраза."""
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            self.browser.navigate(f"{BASE}/search?text={quote(query)}&page={page}", READY, 40)
            data = json.loads(self.browser.evaluate(EXTRACT))
        except (BrowserError, OSError, ValueError, TypeError) as exc:
            raise SourceError(f"браузер: {exc}") from exc
        finally:
            self._last = time.monotonic()
            self.requests_made += 1

        if data.get("captcha"):
            self.rate_limited += 1
            raise SourceError("Маркет показал капчу")
        cards = data.get("cards") or []
        if not cards:
            if page == 1:
                raise SourceError(f"пустая выдача на первой странице: «{data.get('title', '')[:60]}»")
            return Page([], last=True)

        offers, unpriced = [], 0
        for card in cards:
            zone = card["zone"]
            href = (card.get("href") or "").split("?")[0]
            # Идентификатор карточки товара — последний сегмент её адреса
            card_id = href.rstrip("/").rsplit("/", 1)[-1] if href else zone.get("marketSku")
            price, basic = _price(zone)
            if not card_id or price is None:
                unpriced += 1
                continue
            shop = zone.get("shopId") or zone.get("supplierId")
            offers.append(OfferData(
                external_id=str(card_id),
                category_ref=str(zone.get("hid")),
                source_name=(card.get("title") or "").strip(),
                url=BASE + href if href.startswith("/") else href or BASE,
                price=price,
                price_basic=basic,
                seller=f"магазин {shop}" if shop else None,
                raw={"zone": zone, "shown": card.get("shown")},
            ))
        return Page(offers, unpriced, last=False)

    def close(self) -> None:
        self.browser.close()
