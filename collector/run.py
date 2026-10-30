"""Запуск сбора.

    .venv-win\\Scripts\\python.exe -m collector.run wb --category gpu --pages 2
    .venv-win\\Scripts\\python.exe -m collector.run dns --category cpu --pages 3
    .venv-win\\Scripts\\python.exe -m collector.run ozon --category gpu --pages 15

Что собирать, задаёт таблица collect_targets: у каждой площадки и
категории свой запрос — поисковая фраза у WB, раздел каталога у DNS и Ozon.
Страница выдачи у площадок разного размера: 100 товаров у WB, 24 у DNS,
8 у Ozon, 20 у AliExpress, 8–16 у Яндекс Маркета.
Каждый запрос — отдельный сеанс сбора с записью в журнал. Предложение
создаётся при первой встрече и обновляется при следующих, а цена каждый
раз пишется новым срезом: так копится история цен.
"""
from __future__ import annotations

import argparse
import sys
import traceback

import psycopg
from psycopg.types.json import Jsonb

from collector.adapters.ali import AliExpressAdapter
from collector.adapters.base import Adapter, SourceError
from collector.adapters.dns import DnsAdapter
from collector.adapters.ozon import OzonAdapter
from collector.adapters.wb import WildberriesAdapter
from collector.adapters.ym import YandexMarketAdapter
from collector.db import connect

ADAPTERS: dict[str, type[Adapter]] = {
    "wb": WildberriesAdapter, "dns": DnsAdapter, "ozon": OzonAdapter,
    "ali": AliExpressAdapter, "ym": YandexMarketAdapter,
}
CATEGORIES = ("cpu", "mb", "ram", "gpu", "psu", "case", "cooler", "ssd", "hdd")

UPSERT_OFFER = """
INSERT INTO offers (marketplace_id, category_id, external_id, source_name, brand,
                    seller, url, rating, feedbacks, raw)
VALUES (%(mp)s, %(cat)s, %(ext)s, %(name)s, %(brand)s,
        %(seller)s, %(url)s, %(rating)s, %(feedbacks)s, %(raw)s)
ON CONFLICT (marketplace_id, external_id) DO UPDATE SET
    source_name = EXCLUDED.source_name, brand = EXCLUDED.brand,
    seller = EXCLUDED.seller, url = EXCLUDED.url, rating = EXCLUDED.rating,
    feedbacks = EXCLUDED.feedbacks, raw = EXCLUDED.raw, last_seen_at = now()
RETURNING id, (xmax = 0) AS inserted
"""

INSERT_SNAPSHOT = """
INSERT INTO price_snapshots (offer_id, session_id, price, price_basic, in_stock, quantity)
VALUES (%s, %s, %s, %s, %s, %s)
"""

FINISH_SESSION = """
UPDATE collect_sessions SET finished_at = now(), status = %(status)s, error = %(error)s,
    pages = %(pages)s, received = %(received)s, new_offers = %(new_offers)s,
    skipped = %(skipped)s
WHERE id = %(id)s
"""


def _lookup(conn: psycopg.Connection, table: str, code: str) -> int:
    row = conn.execute(f"SELECT id FROM {table} WHERE code = %s", (code,)).fetchone()
    if row is None:
        raise SystemExit(f"в таблице {table} нет кода {code}")
    return row[0]


def collect(conn: psycopg.Connection, adapter: Adapter, category: str, pages: int,
            start_page: int = 1) -> None:
    mp_id = _lookup(conn, "marketplaces", adapter.code)
    cat_id = _lookup(conn, "categories", category)
    allowed = {row[0] for row in conn.execute(
        "SELECT external_id FROM marketplace_categories"
        " WHERE marketplace_id = %s AND category_id = %s", (mp_id, cat_id))}
    queries = [row[0] for row in conn.execute(
        "SELECT query FROM collect_targets"
        " WHERE marketplace_id = %s AND category_id = %s AND active ORDER BY id",
        (mp_id, cat_id))]
    if not allowed or not queries:
        print(f"[{adapter.code}/{category}] не настроено: нет категории площадки"
              f" или задания сбора — пропуск")
        return

    for query in queries:
        session_id = conn.execute(
            "INSERT INTO collect_sessions (marketplace_id, category_id, query)"
            " VALUES (%s, %s, %s) RETURNING id", (mp_id, cat_id, query)).fetchone()[0]
        conn.commit()
        stats = {"pages": 0, "received": 0, "new_offers": 0, "skipped": 0}
        status, error = "ok", None
        # Выдача соседних страниц может повторять позиции: один срез на сеанс
        seen: set[str] = set()
        print(f"[{adapter.code}/{category}] «{query}» — сеанс {session_id}")
        try:
            for page_no in range(start_page, pages + 1):
                page = adapter.search(query, page_no)
                stats["pages"] += 1
                stats["skipped"] += page.unpriced
                fresh = 0
                for offer in page.offers:
                    if not adapter.accepts(offer, category, allowed):
                        stats["skipped"] += 1
                        continue
                    if offer.external_id in seen:
                        continue
                    seen.add(offer.external_id)
                    offer_id, inserted = conn.execute(UPSERT_OFFER, {
                        "mp": mp_id, "cat": cat_id, "ext": offer.external_id,
                        "name": offer.source_name, "brand": offer.brand,
                        "seller": offer.seller, "url": offer.url, "rating": offer.rating,
                        "feedbacks": offer.feedbacks, "raw": Jsonb(offer.raw),
                    }).fetchone()
                    conn.execute(INSERT_SNAPSHOT, (
                        offer_id, session_id, offer.price, offer.price_basic,
                        offer.in_stock, offer.quantity))
                    stats["received"] += 1
                    fresh += int(inserted)
                stats["new_offers"] += fresh
                conn.commit()
                print(f"  стр. {page_no}: принято {len(page.offers)}, новых {fresh},"
                      f" без цены {page.unpriced}")
                if page.last:
                    break
        except SourceError as exc:
            conn.rollback()
            # Страницы, записанные до отказа, остаются: сеанс частичный
            status = "partial" if stats["pages"] else "failed"
            error = str(exc)
            print(f"  отказ источника: {exc}")
        except BaseException:
            conn.rollback()
            status, error = "failed", traceback.format_exc(limit=4)
            raise
        finally:
            conn.execute(FINISH_SESSION, {"id": session_id, "status": status,
                                          "error": error, **stats})
            conn.commit()
        print(f"  итог: {status}, позиций {stats['received']},"
              f" новых {stats['new_offers']}, отсеяно {stats['skipped']}")


def main() -> None:
    # Консоль Windows по умолчанию не в UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Сбор предложений с торговых площадок")
    parser.add_argument("marketplace", choices=sorted(ADAPTERS))
    parser.add_argument("--category", action="append", choices=CATEGORIES,
                        help="категория; можно указать несколько, по умолчанию все")
    parser.add_argument("--pages", type=int, default=1,
                        help="до какой страницы выдачи собирать")
    # Лимит частоты у площадок скудный: при доборе незачем заново тянуть
    # страницы, которые уже есть в базе
    parser.add_argument("--start-page", type=int, default=1,
                        help="с какой страницы начинать, для добора пропущенных")
    args = parser.parse_args()

    adapter = ADAPTERS[args.marketplace]()
    try:
        with connect() as conn:
            for category in args.category or CATEGORIES:
                collect(conn, adapter, category, args.pages, args.start_page)
    finally:
        # Браузерные адаптеры держат открытый Chrome
        adapter.close()
    print(f"запросов к площадке: {adapter.requests_made},"
          f" отказов по частоте: {adapter.rate_limited}")


if __name__ == "__main__":
    main()
