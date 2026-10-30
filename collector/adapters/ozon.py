"""Адаптер Ozon: внутренний интерфейс витрины, вызываемый из вкладки браузера.

Ozon собирает страницу из JSON, который отдаёт entrypoint-api. Прямой
запрос к нему из программы получает 403 даже с cookie браузера: защита
распознаёт не-браузер по параметрам соединения. Тот же запрос, выполненный
через fetch() изнутри открытой вкладки ozon.ru, проходит (проверено
11.09.2026). Поэтому адаптер держит Chrome с открытым сайтом и выполняет
запросы в нём; выдачу разбирает прямо в странице и получает наружу только
компактные карточки товаров.

Выдача отдаётся порциями по 8 товаров. Адрес следующей порции с одноразовым
токеном лежит в состоянии виджета infiniteVirtualPaginator, поэтому
страницы проходятся строго по цепочке.
"""
from __future__ import annotations

import json
import re
import time
from decimal import Decimal
from typing import Callable

from collector.adapters.base import Adapter, OfferData, Page, SourceError
from collector.browser import Browser, BrowserError

BASE = "https://www.ozon.ru"
MIN_INTERVAL = 3.0
PAGE_SIZE = 8

# Запрос порции выдачи и её разбор выполняются в странице. Название
# выбирается среди текстовых полей карточки: там же лежит «2 шт осталось»,
# поэтому первое попавшееся текстовое поле названием не является.
FETCH = r"""
(async (u) => {
  const r = await fetch('/api/entrypoint-api.bx/page/json/v2?url=' + encodeURIComponent(u),
                        {headers: {Accept: 'application/json'}, credentials: 'include'});
  const t = await r.text();
  let j;
  try { j = JSON.parse(t) } catch (e) { return JSON.stringify({status: r.status, error: t.slice(0, 200)}) }
  const ws = j.widgetStates || {};
  let items = [], next = j.nextPage || null;
  for (const [k, v] of Object.entries(ws)) {
    let s; try { s = JSON.parse(v) } catch (e) { continue }
    if (k.startsWith('tileGrid')) items = items.concat(s.items || []);
    if (!next && s && typeof s.nextPage === 'string') next = s.nextPage;
  }
  const out = items.map(it => {
    const atoms = it.mainState || [];
    const texts = atoms.filter(a => a.type === 'textDS').map(a => a.textDS || {});
    const stockRe = /шт\.? осталось/;
    const named = texts.filter(x => x.text && !stockRe.test(x.text))
                       .sort((a, b) => b.text.length - a.text.length)[0];
    const prices = ((atoms.find(a => a.type === 'priceV2') || {}).priceV2 || {}).price || [];
    const stock = (texts.find(x => stockRe.test(x.text || '')) || {}).text || null;
    return {sku: String(it.sku || ''), link: (it.action || {}).link || null,
            name: named ? named.text : null,
            prices: prices.map(p => [p.text, p.textStyle]), stock};
  });
  return JSON.stringify({status: r.status, next, items: out});
})(%s)
"""

HOME_READY = "document.body && document.body.innerText.length > 500"


def _money(text: str | None) -> Decimal | None:
    """«206 955 ₽» → 206955. Разряды разделены узким неразрывным пробелом."""
    digits = re.sub(r"[^\d]", "", text or "")
    return Decimal(digits) if digits else None


class OzonAdapter(Adapter):
    code = "ozon"
    page_size = PAGE_SIZE

    def __init__(self, log: Callable[[str], None] = print):
        self.browser = Browser()
        self.log = log
        self._opened = False
        self._last = 0.0
        # Для каждого раздела — номер следующей страницы и её адрес с токеном
        self._next: dict[str, tuple[int, str]] = {}
        self.requests_made = 0
        self.rate_limited = 0

    def search(self, query: str, page: int) -> Page:
        """Порция выдачи раздела; ``query`` — путь раздела каталога."""
        if page == 1:
            url = query
        else:
            stored = self._next.get(query)
            if stored is None or stored[0] != page:
                # Токен следующей страницы выдаёт только предыдущая:
                # чтобы начать не с первой, цепочку приходится пройти
                for earlier in range(1, page):
                    if self.search(query, earlier).last:
                        return Page([], last=True)
                stored = self._next.get(query)
                if stored is None:
                    return Page([], last=True)
            url = stored[1]

        data = self._fetch(url)
        if data.get("status") != 200 or "error" in data:
            raise SourceError(f"HTTP {data.get('status')}: {str(data.get('error'))[:100]}")

        if data.get("next"):
            self._next[query] = (page + 1, data["next"])
        else:
            self._next.pop(query, None)

        # Раздел однозначно задаёт категорию: «/category/videokarty-15721/» → 15721
        category_ref = query.strip("/").rsplit("-", 1)[-1]
        offers, unpriced = [], 0
        for item in data.get("items", []):
            prices = dict((style, text) for text, style in item.get("prices", []))
            price = _money(prices.get("PRICE"))
            if not item.get("sku") or price is None:
                unpriced += 1
                continue
            stock = re.search(r"(\d+)", item.get("stock") or "")
            link = (item.get("link") or "").split("?")[0]
            offers.append(OfferData(
                external_id=item["sku"],
                category_ref=category_ref,
                source_name=(item.get("name") or "").strip(),
                url=BASE + link if link.startswith("/") else link or f"{BASE}{query}",
                price=price,
                price_basic=_money(prices.get("ORIGINAL_PRICE")),
                # Остаток Ozon показывает, только когда товара мало
                quantity=int(stock.group(1)) if stock else None,
                raw=item,
            ))
        return Page(offers, unpriced, last=not data.get("next"))

    def _fetch(self, url: str) -> dict:
        wait = MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            if not self._opened:
                # Запросы выполняются от имени открытой страницы ozon.ru
                if not self.browser.navigate(BASE + "/", HOME_READY, 40):
                    raise SourceError("главная страница Ozon не загрузилась")
                self._opened = True
            return json.loads(self.browser.evaluate(FETCH % json.dumps(url)))
        except (BrowserError, OSError, ValueError, TypeError) as exc:
            raise SourceError(f"браузер: {exc}") from exc
        finally:
            self._last = time.monotonic()
            self.requests_made += 1

    def close(self) -> None:
        self.browser.close()
