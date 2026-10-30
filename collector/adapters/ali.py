"""Адаптер AliExpress: поисковый интерфейс витрины с cookie браузера.

Поиск отдаёт POST /aer-webapi/v1/search. Без cookie ответ пустой, с cookie,
полученными настоящим браузером, — полная выдача (проверено 11.09.2026).
Поэтому Chrome открывается ненадолго, только чтобы получить свежие cookie,
а запросы выполняются напрямую.

Параметр catId этого интерфейса выдачу не фильтрует: поиск идёт по тексту.
Категория в карточке (itemData.properties.categoryId) тоже не годится для
отбора: 11.09.2026 процессоры Ryzen пришли под номерами 470, 5539, 5853 и
6094, причём под 470 лежали и мини-ПК, а под 5539 — и видеокарты. Поэтому
чужие товары отсеиваются по названию: у категории есть слова, без которых
товар к ней не относится, и слова, с которыми точно не относится.

Карточка товара завёрнута в разметку интерфейса, но данные лежат и в
чистом блоке itemData.pdpInfo: ссылка с кодом товара, название, цена.
Доставка у AliExpress оплачивается отдельно и сохраняется в raw.
"""
from __future__ import annotations

import re
import time
from decimal import Decimal
from typing import Callable

import requests

from collector.adapters.base import Adapter, OfferData, Page, SourceError
from collector.browser import Browser, BrowserError

BASE = "https://aliexpress.ru"
SEARCH_URL = BASE + "/aer-webapi/v1/search"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"),
    "Content-Type": "application/json",
    "bx-v": "2.5.37",
    "Origin": BASE,
    "Referer": BASE + "/",
}
MIN_INTERVAL = 5.0
# 11.09.2026 после ~30 запросов подряд один ответ пришёл с кодом 405
BACKOFF = (20, 60, 120)

# Готовые системы попадают в выдачу любой категории. «Ноутбук» в этот
# список не входит: SSD и память «для ноутбука и ПК» подходят и в ПК
_SYSTEMS = r"мини[- ]?пк|mini ?pc|планшет|моноблок|системный блок|игровой пк"
_LAPTOP = r"ноутбук|laptop|notebook|macbook"

# Правила отбора по названию: (без чего не относится, с чем не относится)
TITLE_RULES: dict[str, tuple[str, str]] = {
    "cpu": (r"процессор|\bcpu\b|ryzen|core ?i[3579]|\bi[3579][- ]\d{4}|core ultra|xeon|athlon|pentium|celeron",
            _SYSTEMS + "|" + _LAPTOP + r"|материнск|motherboard|кулер|вентилятор|охла|термопаст|сокет|док"
                                       r"|комплект|набор|\bkit\b|combo"),
    # «Комплект материнской платы» у Али часто — плата с заглушкой, а не
    # набор с процессором, поэтому отсеиваются только наборы с процессором.
    # «С процессорами Ryzen» — это поддержка, а не комплектация
    "mb": (r"материнск|motherboard|mainboard",
           _SYSTEMS + "|" + _LAPTOP + r"|с процессором\b|\+ ?(i[3579]|ryzen|процессор)|\bи \d ?[x×*] ?\d+ ?гб"
                                      r"|\bnas\b|заглушк|i/o|кабел|батаре"),
    "ram": (r"оперативн|\bозу\b|\bddr[345]\b|dimm|memory",
            _SYSTEMS + r"|so-?dimm|\becc\b|\breg\b|rdimm|сервер"),
    "gpu": (r"видеокарт|graphics card|\b[rg]tx ?\d|\brx ?\d{3,4}|radeon|geforce",
            _SYSTEMS + "|" + _LAPTOP + r"|вентилятор|кулер|\bfan\b|кронштейн|держател|подставк|райзер|riser"
                                       r"|кабел|переходник|адаптер|док[- ]?станц|dock|oculink|egpu|onexplayer"
                                       r"|термопрокладк|термопаст|радиатор|бэкплейт|backplate|подсветк|пульт|водоблок"),
    "psu": (r"блок питания|источник питания|power supply|\bpsu\b",
            _LAPTOP + r"|адаптер|кабел|удлинител|тестер|переходник|светодиод|\bled\b"
                      r"|видеонаблюд|импульсн|\bdin\b|зарядн|внешн"),
    "case": (r"корпус|\bcase\b|chassis",
             _LAPTOP + r"|планшет|корпус для (жестк|диск|ssd|hdd|m\.2|nvme|raspberry|телефон)"
                       r"|вентилятор(ы)? для корпус|корпусн\w* вентилятор|ножк|пылев|фильтр"),
    "cooler": (r"кулер|охла|cooler|cooling|радиатор|водян|\bсво\b|\baio\b",
               _SYSTEMS + "|" + _LAPTOP + r"|видеокарт|термопаст|для корпус|корпусн|телефон|подставк"
                                          r"|m\.2|ssd|nvme|памят|ddr|блок питания"),
    # Просто «внешн» не годится: продавцы пишут «для внешней торговли»
    "ssd": (r"\bssd\b|твердотельн|nvme|m\.2",
            _SYSTEMS + r"|внешн\w* (жестк|ssd|твердотельн|накопит|диск)|мобильн|portable|портатив|\busb\b"
                       r"|корпус|бокс|enclosure|адаптер|переходник|радиатор|кулер|охла|док|кабел"),
    # В категории HDD у Али лежат и корпуса-боксы, и кабели; б/у диски не берём
    "hdd": (r"жестк\w* диск|\bhdd\b|hard (disk|drive)",
            _SYSTEMS + "|" + _LAPTOP + r"|внешн|portable|портатив|\busb\b|корпус|бокс|enclosure|адаптер|переходник"
                                       r"|док|кабел|салазк|\bssd\b|б/у|\bб у\b|used|восстановлен|refurb"),
}
_RULES = {cat: (re.compile(inc, re.I), re.compile(exc, re.I)) for cat, (inc, exc) in TITLE_RULES.items()}


def title_fits(title: str, category: str) -> bool:
    """Относится ли товар с таким названием к категории."""
    include, exclude = _RULES[category]
    return bool(include.search(title)) and not exclude.search(title)


class AliExpressAdapter(Adapter):
    code = "ali"
    page_size = 20

    def __init__(self, log: Callable[[str], None] = print):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.log = log
        self._cookies_ready = False
        self._last = 0.0
        self.requests_made = 0
        self.rate_limited = 0

    def _refresh_cookies(self) -> None:
        """Открыть сайт в браузере и забрать выданные ему cookie."""
        browser = Browser()
        try:
            browser.navigate(BASE + "/", "document.body && document.body.innerText.length > 300", 40)
            # Защитный скрипт дописывает cookie уже после загрузки страницы
            time.sleep(4)
            cookies = browser.call("Network.getCookies", {"urls": [BASE + "/"]}).get("cookies", [])
        except (BrowserError, OSError) as exc:
            raise SourceError(f"браузер: {exc}") from exc
        finally:
            browser.close()
        if not cookies:
            raise SourceError("браузер не получил cookie AliExpress")
        self.session.cookies.clear()
        for c in cookies:
            self.session.cookies.set(c["name"], c["value"], domain=c.get("domain"), path=c.get("path", "/"))
        self._cookies_ready = True
        self.log(f"    cookie AliExpress обновлены: {len(cookies)}")

    def search(self, query: str, page: int) -> Page:
        """Страница выдачи; ``query`` — поисковая фраза."""
        items = self._items(query, page)
        # Пустая первая страница — не конец выдачи, а мягкий отказ: так
        # AliExpress отвечал 11.09.2026 после ~40 запросов за ночь
        if not items and page == 1:
            raise SourceError("пустая выдача на первой странице — площадка ограничивает запросы")
        offers, unpriced = [], 0
        for item in items:
            data = item.get("snippetContainer", {}).get("itemData", {})
            info = data.get("pdpInfo", {})
            pre = info.get("preloadedData", {})
            tracking = (data.get("trackingInfo") or {}).get("webTrackInfo", {}).get("aerEvent", {})
            path = re.search(r"/item/([\d_]+)\.html", info.get("url", ""))
            code = self._item_id(tracking, path.group(1) if path else "")
            value = (pre.get("price") or {}).get("value")
            if not code or not value:
                unpriced += 1
                continue
            store = re.search(r"/store/(\d+)", (pre.get("store") or {}).get("url", ""))
            offers.append(OfferData(
                external_id=code,
                category_ref=str((data.get("properties") or {}).get("categoryId")),
                source_name=(pre.get("title") or "").strip(),
                # Адрес сохраняется как есть: у части товаров он с префиксом «1_»
                url=f"{BASE}/item/{path.group(1)}.html" if path else f"{BASE}/item/{code}.html",
                price=Decimal(str(value)),
                seller=f"магазин {store.group(1)}" if store else None,
                raw={"pdpInfo": info, "properties": data.get("properties"),
                     "deliveryPrice": tracking.get("deliveryPrice"),
                     "soldCount": tracking.get("soldCount")},
            ))
        return Page(offers, unpriced, last=not items)

    def accepts(self, offer: OfferData, category: str, allowed: set[str]) -> bool:
        # Номер категории AliExpress ненадёжен, см. описание модуля
        return title_fits(offer.source_name, category)

    @staticmethod
    def _item_id(tracking: dict, path: str) -> str | None:
        """Номер товара: из данных трекинга, иначе из адреса.

        У части товаров адрес имеет вид /item/1_772684063.html: номер идёт
        после префикса, и разбор адреса до первой нецифры даёт «1». Так
        11.09.2026 разные товары склеились в одну запись.
        """
        item_id = tracking.get("itemId")
        if item_id:
            return str(item_id)
        tail = path.rsplit("_", 1)[-1]
        return tail if len(tail) > 6 and tail.isdigit() else None

    def _items(self, query: str, page: int) -> list[dict]:
        payload = {
            "catId": "", "searchInfo": "", "searchText": query, "storeIds": [],
            "pgChildren": [], "aeBrainIds": [], "searchTrigger": "search_bar",
            "mainFilters": "", "source": "direct", "page": page,
        }
        if not self._cookies_ready:
            self._refresh_cookies()
        refreshed = False
        for attempt, pause in enumerate((0, *BACKOFF), start=1):
            if pause:
                self.log(f"    отказ AliExpress — пауза {pause} с, попытка {attempt}")
                time.sleep(pause)
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            try:
                response = self.session.post(SEARCH_URL, json=payload, timeout=30)
            except (requests.Timeout, requests.ConnectionError):
                continue
            finally:
                self._last = time.monotonic()
                self.requests_made += 1
            if response.status_code in (405, 429):
                self.rate_limited += 1
                continue
            if response.status_code != 200:
                raise SourceError(f"HTTP {response.status_code}")
            try:
                feed = (response.json().get("data") or {}).get("productsFeed") or {}
            except ValueError:
                feed = None
            if feed is None or (not feed and not refreshed):
                # Пустой ответ без ошибки — признак протухших cookie
                self._refresh_cookies()
                refreshed = True
                continue
            return feed.get("productsV2") or []
        raise SourceError(f"{len(BACKOFF) + 1} отказов подряд")

    def close(self) -> None:
        self.session.close()
