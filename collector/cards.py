"""Характеристики с карточек товаров: Wildberries, DNS, Ozon, Яндекс Маркет.

    .venv-win\\Scripts\\python.exe -m collector.cards wb                  # gpu, case, cooler, mb
    .venv-win\\Scripts\\python.exe -m collector.cards dns --category gpu --days 30

Карточка WB — статический card.json на CDN basket-NN.wbbasket.ru. Номер
корзины NN зависит от диапазона vol = nm // 100000, и диапазоны WB со временем
дописывает, поэтому таблицы нет: корзина угадывается по ближайшему меньшему
уже найденному vol (номера растут вместе с vol) и уточняется перебором.
Предложения идут по возрастанию nm, так что угадывание почти всегда верно.

DNS и Ozon пускают к своим интерфейсам только браузер (напрямую — 401/403),
поэтому запросы выполняются fetch'ем из открытой вкладки площадки:
- DNS: полный список отдаёт get-product-characteristics-actual по полному
  guid товара (его сохраняет сборщик в raw.card.guid);
- Ozon: виджет webCharacteristics второй «страницы» раскладки товара
  в entrypoint-api.
У Яндекс Маркета интерфейса нет — открывается страница <карточка>/spec.

Берутся предложения, привязанные к модели, у которых характеристик ещё нет
или они старше --days дней. Результат — таблица offer_props.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from urllib.parse import urlsplit

import requests
from psycopg.types.json import Jsonb

from collector.adapters import dns, ozon
from collector.adapters.wb import HEADERS
from collector.browser import Browser, BrowserError
from collector.db import connect

TODO = """
SELECT o.id, o.external_id, o.url, o.raw -> 'card' ->> 'guid' FROM offers o
JOIN marketplaces mp ON mp.id = o.marketplace_id
JOIN categories c    ON c.id = o.category_id
LEFT JOIN offer_props p ON p.offer_id = o.id
WHERE mp.code = %s AND c.code = ANY(%s) AND o.model_id IS NOT NULL
  AND (p.offer_id IS NULL OR p.fetched_at < now() - make_interval(days => %s))
ORDER BY length(o.external_id), o.external_id
"""
UPSERT = """
INSERT INTO offer_props (offer_id, props) VALUES (%s, %s)
ON CONFLICT (offer_id) DO UPDATE SET props = EXCLUDED.props, fetched_at = now()
"""


class CardError(Exception):
    """Площадка перестала отдавать карточки — сбор надо прервать, а не
    записывать пустые характеристики."""


def pairs_to_props(pairs) -> dict:
    """{название: значение}; при повторе названия остаётся первое значение."""
    props = {}
    for name, value in pairs:
        if name and value not in (None, ""):
            props.setdefault(str(name).strip(), str(value).strip())
    return props


class WbCards:
    CARD_URL = "https://basket-{b:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json"
    MAX_BASKET = 50
    MIN_INTERVAL = 0.2

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.known: dict[int, int] = {}  # vol → корзина
        self._last = 0.0
        self.requests_made = 0

    def _order(self, vol: int) -> list[int]:
        lower = [v for v in self.known if v <= vol]
        ref = self.known[max(lower)] if lower else self.known[min(self.known)] if self.known else 1
        # Сначала вверх от угаданной: при большем vol корзина не меньше
        return [*range(ref, self.MAX_BASKET + 1), *range(ref - 1, 0, -1)]

    def _get(self, url: str):
        wait = self.MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                return self.session.get(url, timeout=15)
            except requests.RequestException:
                time.sleep(2 * (attempt + 1))
            finally:
                self._last = time.monotonic()
                self.requests_made += 1
        return None

    def props(self, external_id: str, url: str, guid: str | None) -> dict:
        nm = int(external_id)
        vol, part = nm // 100000, nm // 1000
        for b in self._order(vol):
            r = self._get(self.CARD_URL.format(b=b, vol=vol, part=part, nm=nm))
            if r is not None and r.status_code == 200:
                self.known[vol] = b
                card = r.json()
                return pairs_to_props(
                    (o.get("name"), o.get("value")) for o in
                    [*card.get("options", []),
                     *(o for g in card.get("grouped_options", []) for o in g.get("options", []))])
        return {}  # карточки нет ни в одной корзине — товар снят

    def close(self):
        self.session.close()


class TabCards:
    """Запросы fetch'ем из открытой вкладки площадки. FETCH — JavaScript,
    который получает адрес и возвращает JSON {status, pairs}; pairs = null,
    если площадка не ответила."""
    HOME = READY = FETCH = ""
    MIN_INTERVAL = 1.0

    def __init__(self):
        self.browser = Browser()
        self._ready = False
        self._last = 0.0
        self.requests_made = 0

    def _pairs(self, target: str) -> list:
        status = None
        for attempt in range(3):
            if not self._ready:
                # Вкладка нужна ради cookie защиты и того же источника для fetch
                self.browser.navigate(self.HOME, self.READY, timeout=40)
                self._ready = True
            wait = self.MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                d = json.loads(self.browser.evaluate(self.FETCH % json.dumps(target)))
            except BrowserError as exc:
                # «Failed to fetch» — вкладка ушла со страницы площадки или сеть моргнула
                d = {"status": str(exc), "pairs": None}
            self._last = time.monotonic()
            self.requests_made += 1
            status = d["status"]
            if d["pairs"] is not None:
                return d["pairs"]
            # 401/403 — защита сбросила сеанс: открыть вкладку заново
            self._ready = False
            time.sleep(5 * (attempt + 1))
        raise CardError(f"{type(self).__name__}: HTTP {status}, три попытки")

    def close(self):
        self.browser.close()


class DnsCards(TabCards):
    HOME, READY = dns.BASE + "/", "true"
    URL = dns.BASE + "/catalog/product/get-product-characteristics-actual/?id="
    FETCH = r"""
fetch(%s, {headers: {'X-Requested-With': 'XMLHttpRequest'}}).then(async r => {
  const out = {status: r.status, pairs: null};
  if (r.ok) {
    try {
      const doc = new DOMParser().parseFromString((await r.json()).html || '', 'text/html');
      const txt = el => (el?.textContent || '').replace(/\s+/g, ' ').trim();
      out.pairs = [...doc.querySelectorAll('.product-characteristics__spec')].map(li => [
        txt(li.querySelector('.product-characteristics__spec-title')),
        txt(li.querySelector('.product-characteristics__spec-value'))]);
    } catch (e) { out.error = String(e); }
  }
  return JSON.stringify(out);
})
"""

    def props(self, external_id: str, url: str, guid: str | None) -> dict:
        return pairs_to_props(self._pairs(self.URL + guid)) if guid else {}


class OzonCards(TabCards):
    HOME, READY = ozon.BASE + "/", ozon.HOME_READY
    MIN_INTERVAL = 1.5
    FETCH = r"""
(async () => {
  const page = %s + '?layout_container=pdpPage2column&layout_page_index=2';
  const r = await fetch('/api/entrypoint-api.bx/page/json/v2?url=' + encodeURIComponent(page));
  const out = {status: r.status, pairs: null};
  if (r.ok) {
    try {
      const ws = (await r.json()).widgetStates || {};
      out.pairs = [];
      for (const [k, v] of Object.entries(ws)) {
        if (!k.startsWith('webCharacteristics')) continue;
        for (const block of JSON.parse(v).characteristics || [])
          for (const it of [...(block.short || []), ...(block.long || [])])
            out.pairs.push([it.name, (it.values || []).map(x => x.text).join(', ')]);
      }
    } catch (e) { out.error = String(e); }
  }
  return JSON.stringify(out);
})()
"""
    # Единицу Ozon пишет в названии поля: «Высота, см: 39.5». Она переносится
    # в значение, иначе 39,5 см прочитаются как миллиметры.
    UNIT = re.compile(r",\s*(мм|см)$")

    def props(self, external_id: str, url: str, guid: str | None) -> dict:
        pairs = []
        for name, value in self._pairs(urlsplit(url).path):
            m = self.UNIT.search(name or "")
            if m and re.fullmatch(r"\d+(?:[.,]\d+)?", value or ""):
                value = f"{value} {m.group(1)}"
            pairs.append((name, value))
        return pairs_to_props(pairs)


class YmCards:
    """Яндекс Маркет: интерфейса с характеристиками нет — открывается страница
    <карточка>/spec, строки data-auto="product-spec-item" разбираются в ней:
    первый блок строки — название, последний — значение."""
    MIN_INTERVAL = 3.0
    READY = "document.querySelector('[data-auto=\"product-spec-item\"]') !== null"
    EXTRACT = r"""JSON.stringify([...document.querySelectorAll('[data-auto="product-spec-item"]')].map(el => [
  el.firstElementChild?.innerText.replace(/\s+/g, ' ').trim(), el.lastElementChild?.innerText.replace(/\s+/g, ' ').trim()]))"""

    def __init__(self):
        self.browser = Browser()
        self._last = 0.0
        self.requests_made = 0

    def props(self, external_id: str, url: str, guid: str | None) -> dict:
        wait = self.MIN_INTERVAL - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        ready = self.browser.navigate(url.split("?")[0].rstrip("/") + "/spec", self.READY, timeout=30)
        self._last = time.monotonic()
        self.requests_made += 1
        if not ready:
            title = self.browser.evaluate("document.title") or ""
            if "Маркет" not in title:
                raise CardError(f"ЯМ не отдал страницу: «{title[:60]}» — возможно, капча в окне Chrome")
            return {}  # у карточки нет характеристик
        return pairs_to_props(json.loads(self.browser.evaluate(self.EXTRACT)))

    def close(self):
        self.browser.close()


SOURCES = {"wb": WbCards, "dns": DnsCards, "ozon": OzonCards, "ym": YmCards}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("marketplace", choices=list(SOURCES))
    ap.add_argument("--category", action="append", help="по умолчанию gpu, case, cooler, mb")
    ap.add_argument("--days", type=int, default=30, help="через сколько дней обновлять характеристики")
    args = ap.parse_args()
    cats = args.category or ["gpu", "case", "cooler", "mb"]

    src = SOURCES[args.marketplace]()
    with connect() as conn:
        todo = conn.execute(TODO, (args.marketplace, cats, args.days)).fetchall()
        print(f"карточек к запросу: {len(todo)} ({', '.join(cats)})", flush=True)
        found = empty = 0
        t0 = time.monotonic()
        try:
            for i, (oid, ext, url, guid) in enumerate(todo, 1):
                props = src.props(ext, url, guid)
                found += bool(props)
                empty += not props
                conn.execute(UPSERT, (oid, Jsonb(props)))
                if i % 50 == 0 or i == len(todo):
                    conn.commit()
                    print(f"  {i}/{len(todo)}: с характеристиками {found}, пустых {empty}, "
                          f"запросов {src.requests_made}, {time.monotonic() - t0:.0f} с", flush=True)
        except CardError as exc:
            conn.commit()
            print(f"ПРЕРВАНО: {exc}. Собранное сохранено, повторный запуск продолжит.", flush=True)
            sys.exit(1)
        finally:
            src.close()


if __name__ == "__main__":
    main()
