"""Каталог моделей для веб-интерфейса.

Позиции каталога — модели нормализации (collector.normalize: артикул, модельное
имя эталона, затем признаки). Здесь из названий предложений модели берутся цвет,
форм-фактор и размеры, поверх них — характеристики карточек (collector.cards,
таблица offer_props). Чего нет ни там, ни там, оценивается по классу изделия
и помечается как оценка (поле est).

Результат — web/prototype-3d/catalog.js. Это скрипт, а не JSON: страница
открывается с file://, и загрузка данных через fetch там запрещена.

Запуск (Windows-Python):  .venv-win/Scripts/python.exe -m collector.catalog
"""
from __future__ import annotations

import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from collector import perf
from collector.db import connect

OUT = Path(__file__).resolve().parent.parent / "web" / "prototype-3d" / "catalog.js"

SQL = """
SELECT o.id, c.code, m.code, o.source_name, o.brand, o.url,
       CASE WHEN m.code = 'wb' THEN o.raw -> 'colors' END,
       p.price, p.in_stock, o.model_id, md.name, md.brand, md.article, md.specs, pr.props
FROM offers o
JOIN categories c   ON c.id = o.category_id
JOIN marketplaces m ON m.id = o.marketplace_id
LEFT JOIN models md ON md.id = o.model_id
LEFT JOIN offer_props pr ON pr.offer_id = o.id
JOIN LATERAL (
    SELECT price, in_stock FROM price_snapshots ps
    WHERE ps.offer_id = o.id ORDER BY collected_at DESC LIMIT 1
) p ON true
WHERE c.code IN ('gpu', 'case', 'cooler', 'mb', 'ssd', 'hdd', 'cpu', 'ram', 'psu') AND p.price > 0
"""

# ---------------------------------------------------------------- общее


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("…", " ").replace("ё", "е").replace("Ё", "Е")).strip()


def prop_vals(offs, names, parse) -> list:
    """Значения поля из характеристик карточек (collector.cards) по предложениям
    модели. Площадки называют поля по-разному, поэтому у каждого предложения
    берётся первое из names, которое parse смог разобрать."""
    vals = []
    for o in offs:
        for n in names:
            x = parse(o["props"].get(n) or "")
            if x is not None:
                vals.append(x)
                break
    return vals


def prop_num(offs, names, lo, hi, frac=False) -> int | float | None:
    """Число — медиана по предложениям модели: продавцы ошибаются поодиночке.
    Размеры приходят в мм или в см; значение вне [lo, hi] считается ошибкой
    заполнения. Миллиметры округляются (331,9 мм у продавца — это 332), дробь
    остаётся только с frac: 2,5 слота — не 2, 5,4 ГГц — не 5."""
    def parse(v):
        m = re.match(r"(\d+(?:[.,]\d+)?)\s*(см|cm)?", v)
        if m:
            x = float(m.group(1).replace(",", ".")) * (10 if m.group(2) else 1)
            return x if lo <= x <= hi else None
    vals = prop_vals(offs, names, parse)
    if not vals:
        return None
    x = statistics.median_low(vals)
    return round(x, 1) if frac and x != int(x) else round(x)


def dims3(v: str) -> tuple[float, float, float] | None:
    """Три размера «A×B×C» в мм, в порядке записи: «146х69х19», «100x123x65 мм».
    Порядок осей у продавцов разный — разбирается там, где известно поле."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*[xхХX×*]\s*(\d+(?:[.,]\d+)?)\s*[xхХX×*]\s*(\d+(?:[.,]\d+)?)", v)
    return tuple(float(g.replace(",", ".")) for g in m.groups()) if m else None


def color_of(title: str, wb_colors) -> str | None:
    """Цвет из названия; у WB есть и отдельное поле. Только чёрный и белый:
    других цветов в выдаче единицы, а модели нужен именно этот выбор."""
    t = title.lower()
    for rx, c in ((r"бел(ый|ая|ое|ого|ом)\b", "white"), (r"черн(ый|ая|ое|ого|ом)\b", "black"),
                  (r"\bwhite\b", "white"), (r"\bblack\b", "black"),
                  (r"\bwh\b|\(w\)|-w\b|\bsnow\b|\bice\b|\bfrost\b", "white"), (r"\bbk\b|-bk\b|-b\b", "black")):
        if re.search(rx, t):
            return c
    for c in wb_colors or []:
        name = norm(str(c.get("name", ""))).lower()
        if name.startswith("бел"):
            return "white"
        if name.startswith("черн"):
            return "black"
    return None


def find_vendor(title: str, vendors: list[str], by_series: dict[str, str], brand: str | None) -> str | None:
    """Производитель: самый ранний известный бренд в названии, затем — по
    фирменной серии (VENTUS → MSI), затем — поле бренда площадки."""
    best = None
    for v in vendors:
        m = re.search(r"(?<![A-Za-z0-9])" + re.escape(v) + r"(?![A-Za-z0-9])", title, re.I)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), v)
    if best:
        return best[1]
    up = title.upper()
    for key, v in by_series.items():
        if re.search(r"(?<![A-Z0-9])" + re.escape(key), up):
            return v
    if brand and 1 < len(brand) < 24 and not re.search(r"нет бренда|no brand|noname", brand, re.I):
        for v in vendors:
            if v.lower() == brand.lower():
                return v
        return brand.strip()
    return None


STOP = set("""
корпус корпуса для пк компьютерный компьютера игровой игровая кулер кулера процессора процессорный
процессоров башенный устройство охлаждения охлаждение материнская материнка плата платы видеокарта
без бп с и на в new новая новый официальная гарантия black white черный белый bk wh
atx matx m-atx micro-atx microatx mini-itx itx m-itx e-atx eatx midi middle tower miditower midi-tower
mini-tower mini full full-tower ret rtl oem pwm 4-pin 4pin soc socket lga intel amd
audio bott al+cu cu al gray grey silver rgb argb psu fan fans mid cpu cooler
""".split())


# Кириллица, неотличимая от латиницы: модель «РС800» бывает набрана русскими буквами
HOMO = str.maketrans("АВСЕНКМОРТХаеокрсху", "ABCEHKMOPTXaeokpcxy")


def model_tokens(title: str, vendor: str | None, extra_stop=()) -> list[str]:
    """Значимые слова названия по порядку: без категории, бренда, цвета,
    форм-фактора и перечня характеристик после запятой или скобки."""
    s = re.split(r"[,(\[{]", norm(title))[0]
    s = re.sub(r"\d+\s*[xх]\s*\d+\s*(mm|мм)\b|\d+\s*(mm|мм|cm|см|вт|w|db|дб|гб|gb)\b|usb\s*\S*|\b(с|со)\s+\S+\s+стекл\w*",
               " ", s, flags=re.I)
    vend = set((vendor or "").lower().split())
    out = []
    for tok in re.findall(r"[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9\-.+!]*", s):
        if re.search(r"\d", tok) and re.search(r"[А-Яа-я]", tok):
            lat = tok.translate(HOMO)
            tok = lat if not re.search(r"[А-Яа-я]", lat) else tok
        low = tok.lower().strip(".-")
        # название модели — латиница и цифры; русские слова описательные
        # («сталь», «башня», «для пк») и склеивали бы разные товары
        if not low or low in STOP or low in vend or low in extra_stop or not re.search(r"[a-z0-9]", low):
            continue
        if re.fullmatch(r"(lga|am)\d+.*|ddr\d.*|[\d+]+-?pin|\d|\d+x\d*|x\d+|led|pwm|tdp|soc-\S*|"
                        r"775|1150|1151|1155|1156|1200|1700|1851|2011", low):
            continue
        out.append(tok.strip(".-"))
    return out


def display_name(group, strip_rx: str) -> str:
    """Наименование для списка: у DNS названия самые чистые, иначе — самое короткое."""
    titles = sorted(group, key=lambda o: (o["mp"] != "dns", len(o["title"])))
    t = titles[0]["title"]
    t = re.sub(strip_rx, "", t, flags=re.I)
    t = re.split(r"\s*[\[(,]", t)[0]
    t = re.sub(r"\s+(черный|белый|черная|белая)$", "", t, flags=re.I)
    return norm(t)[:80]


def name_key(toks: list[str], oid: int) -> tuple:
    """Ключ модели — два первых слова, если среди них есть буквы; иначе
    (одни числа) товар ни с чем не склеивается. Дефис делит слово:
    GM-800B и GM 800B — одна модель."""
    head = tuple(p for x in toks for p in x.lower().split("-") if p)[:2]
    return head if any(re.search(r"[a-z]", x) for x in head) else ("#", oid)


def model_name(group, strip_rx: str) -> str:
    """«Бренд + модель» из значимых слов; если модели в названии нет — само название."""
    o = max(group, key=lambda o: len(o["toks"]))
    if o["toks"]:
        return " ".join(filter(None, [o["vendor"], " ".join(o["toks"][:4])]))[:80]
    return re.sub(r"^[\W_]+", "", display_name(group, strip_rx))


def uid(prefix: str, key) -> str:
    return prefix + hashlib.md5(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:8]


OUTLIER = 3  # во сколько раз цена может отличаться от медианы модели

# Адреса площадок: в каталоге ссылки хранятся без них, веб-часть добавляет (SHOP_HOST в app.js)
SHOP_HOST = {"wb": "https://www.wildberries.ru", "ozon": "https://www.ozon.ru", "dns": "https://www.dns-shop.ru",
             "ym": "https://market.yandex.ru", "ali": "https://aliexpress.ru"}


def fix_url(url: str | None) -> str | None:
    """Сборщик DNS клеил адрес к уже полной ссылке: «https://www.dns-shop.ruhttps://…»."""
    return re.sub(r"^https?://[^/]+(?=https?://)", "", url) if url else url


def short_url(mp: str, url: str) -> str:
    """Ссылка без адреса площадки — каталог легче; чужой домен остаётся целиком."""
    url, host = fix_url(url), SHOP_HOST.get(mp)
    return url[len(host):] if host and url.startswith(host + "/") else url


def summarize(offers: list[dict]) -> dict:
    """Цена модели — минимум среди «в наличии». При трёх и более предложениях цены,
    отличающиеся от медианы больше чем в OUTLIER раз, отбрасываются: это чаще чужой
    товар (комплект, б/у, аксессуар) или опечатка продавца. По двум ценам не понять,
    какая из них неверна, — там ничего не отбрасывается."""
    # AliExpress: одно и то же объявление бывает выложено копиями под разными номерами — считать один раз
    seen = set()
    offers = [o for o in offers if o["mp"] != "ali" or (k := (o["title"], o["price"])) not in seen and not seen.add(k)]
    kept = offers
    if len(offers) >= 3:
        m = statistics.median(o["price"] for o in offers)
        kept = [o for o in offers if m / OUTLIER <= o["price"] <= m * OUTLIER]
    stock = [o for o in kept if o["in_stock"]] or kept
    best = min(stock, key=lambda o: o["price"])
    # Ссылка на каждую площадку — самое дешёвое её предложение, «в наличии» первыми
    links = {}
    for o in sorted(kept, key=lambda o: (not o["in_stock"], o["price"])):
        if o["url"]:
            links.setdefault(o["mp"], short_url(o["mp"], o["url"]))
    out = {
        "price": int(best["price"]),
        "offers": len(kept),
        "shops": sorted({o["mp"] for o in kept}),
        "url": fix_url(best["url"]),
        "links": links,
    }
    if len(kept) < len(offers):
        out["dropped"] = len(offers) - len(kept)
    return out


def common(values):
    """Самое частое известное значение — признак модели по её предложениям."""
    vals = [v for v in values if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


# Модели нормализации, слитые с другой (одинаковое название): id слитой → id оставшейся, для истории цен
MERGED: dict[int, int] = {}


def merge_into(merged: dict, key, mid, offs):
    """Группа mid → в merged[key]; первая группа с этим ключом даёт id, остальные запоминаются в MERGED."""
    kept, acc = merged.setdefault(key, (mid, []))
    acc.extend(offs)
    if kept != mid:
        MERGED[mid] = kept


# Признаки из названия, по которым одноимённые модели не сливаются, если спорят (by_model)
PARSED = {"type", "cap", "kit", "freq", "color", "form", "iface", "rpm", "watt", "cert", "chip", "mem", "cpu", "pack",
          "tpl"}


def by_model(rows, junk_rx, parse):
    """Предложения → группы по модели нормализации (collector.normalize).
    Мусор и предложения без модели в каталог не попадают."""
    groups, junk = defaultdict(list), 0
    for o in rows:
        if junk_rx.search(o["title"]) or o["model"] is None:
            junk += 1
            continue
        o.update(parse(o))
        groups[o["model"]].append(o)
    # Нормализация иногда заводит одну деталь несколькими моделями с тем же названием (разные артикулы
    # площадок) — в списке это неотличимые дубли. Сливаем в группу, где больше предложений: её id и остаётся.
    # Только если не спорят признаки, разобранные из названий объявлений (тип и частота памяти, объём, цвет…):
    # «Apacer NOX 16 ГБ» — это и DDR4 2×8, и DDR5 1×16. При разных артикулах нужны одинаковые и известные у обеих
    # признаки (у DNS в названии только артикул и объём — тип памяти неизвестен, значит, не сливаем);
    # при одинаковом или неизвестном артикуле неизвестное (None) ни с чем не спорит.
    merged, sigs = {}, defaultdict(list)          # название → [(признаки, ключ в merged)]
    for mid, offs in sorted(groups.items(), key=lambda g: -len(g[1])):
        name = re.sub(r"[^a-zа-яё0-9]+", " ", offs[0]["md"]["name"].lower()).strip()
        sig = {k: common(o[k] for o in offs) for k in offs[0] if k in PARSED and not isinstance(offs[0][k], list)}
        art = (offs[0]["md"].get("article") or "").upper() or None
        for known, key in sigs[name]:
            strict = art and known["_art"] and art != known["_art"]
            if all(v is not None and v == known.get(k) if strict and k not in ("color", "tpl") else
                   v is None or known.get(k) is None or v == known[k] for k, v in sig.items()):
                known.update({k: v for k, v in sig.items() if known.get(k) is None})
                break
        else:
            key = mid
            sigs[name].append(({**sig, "_art": art}, key))
        merge_into(merged, key, mid, offs)
    return dict(merged.values()), junk


def merge_partial(groups: dict, attrs: tuple[str, ...]):
    """Предложение без памяти или цвета в названии присоединяется к единственной
    группе с тем же ключом, где эти поля указаны — иначе остаётся отдельно."""
    for key in [k for k in groups if any(k[a] is None for a in range(len(attrs)))]:
        if key not in groups:
            continue
        base = {i for i in range(len(attrs))}
        cands = [k for k in groups if k != key and all(
            key[i] is None or key[i] == k[i] for i in base) and all(
            key[j] == k[j] for j in range(len(attrs), len(key)))]
        if len(cands) == 1:
            groups[cands[0]].extend(groups.pop(key))


# ---------------------------------------------------------------- видеокарты

GPU_VENDORS = ["ASRock", "ASUS", "MSI", "GIGABYTE", "Palit", "KFA2", "Galax", "Zotac", "Inno3D", "Colorful",
               "Sapphire", "PowerColor", "XFX", "AFOX", "Maxsun", "Ninja", "Biostar", "PNY", "Gainward",
               "Sinotex", "Manli", "Yeston", "Arktek", "Peladn", "Soyo", "Sparkle", "Acer", "Mllse"]
GPU_SERIES_VENDOR = {"VENTUS": "MSI", "SHADOW": "MSI", "SUPRIM": "MSI", "INSPIRE": "MSI", "GAMING TRIO": "MSI",
                     "GV-": "GIGABYTE", "WINDFORCE": "GIGABYTE", "EAGLE": "GIGABYTE", "AORUS": "GIGABYTE",
                     "AERO": "GIGABYTE", "90YV": "ASUS", "TUF": "ASUS", "PRIME-": "ASUS", "ROG": "ASUS",
                     "PA-RTX": "Palit", "NE7": "Palit", "NE6": "Palit", "JETSTREAM": "Palit", "GAMINGPRO": "Palit",
                     "INFINITY": "Palit", "PULSE": "Sapphire", "NITRO": "Sapphire", "HELLHOUND": "PowerColor",
                     "RED DEVIL": "PowerColor", "FIGHTER": "PowerColor", "CHALLENGER": "ASRock",
                     "STEEL LEGEND": "ASRock", "TAICHI": "ASRock", "AMP": "Zotac", "TRINITY": "Zotac",
                     "TWIN EDGE": "Zotac", "ICHILL": "Inno3D", "AF": "AFOX"}
GPU_SERIES = ["VENTUS", "GAMING", "TRIO", "SHADOW", "INSPIRE", "SUPRIM", "EXPERT", "VANGUARD", "MECH", "ARMOR",
              "EAGLE", "WINDFORCE", "AERO", "AORUS", "MASTER", "ELITE", "DUAL", "TUF", "PRIME", "ROG", "STRIX",
              "ASTRAL", "INFINITY", "JETSTREAM", "GAMINGPRO", "STORMX", "ROCK", "TWIN", "EDGE", "AMP", "TRINITY",
              "SOLID", "PULSE", "NITRO", "PURE", "HELLHOUND", "REAPER", "DEVIL", "FIGHTER", "CHALLENGER", "STEEL",
              "LEGEND", "TAICHI", "SWIFT", "MERC", "ICHILL", "PHANTOM", "SPECTRAL", "ICE", "SNOW", "WHITE",
              "SFF", "MAX", "PLUS", "PRO", "2X", "3X", "X2", "X3", "LP", "ITX", "MINI", "OC"]
GPU_JUNK = re.compile(
    r"подходит для|для видеокарт|(вентилятор|кулер|радиатор|кронштейн|держател|подставк|райзер|riser|кабел|"
    r"шлейф|термопрокладк|бэкплейт|backplate|защитн\w* плат)\w*\s+(для|к)\b|запчаст|ремонт|^\W*(вентилятор|кулер)|"
    r"мобильн|mining|майнинг|quadro|(?<![a-z])cmp\s*-?\d|(?<![a-z])p10[246]-",
    re.I)
CHIP = re.compile(r"(?<![A-Za-z])(RTX|GTX|GT|RX|ARC)\s*-?\s*([AB]?\d{3,4})\s*(TI|SUPER|XTX|XT|GRE)?\b"
                  r"(?:\s*(TI|SUPER|XT|GRE)\b)?", re.I)
SUFFIX = {"TI": "Ti", "SUPER": "Super", "XT": "XT", "XTX": "XTX", "GRE": "GRE"}


def _nv(num: str, ti: bool = False):
    return ("GTX" if num.startswith("1") else "RTX"), num, "Ti" if ti else ""


# Когда чипа нет в тексте: старые линейки и заводские артикулы производителей
GPU_FALLBACK = [
    (r"(?<![A-Za-z0-9])(?:GEFORCE\s*|GF\s*|G\s*)210(?!\d)", lambda m: ("GT", "210", "")),
    (r"(?<![A-Za-z0-9])(R[579])[\s-]+(\d{3})(?!\d)", lambda m: (m[1].upper(), m[2], "")),   # не R9700
    (r"(?<![A-Za-z0-9])HD\s*-?\s*(\d{4})(?!\d)", lambda m: ("HD", m[1], "")),
    (r"RADEON\s+(\d{4})\s*(XT|GRE)?", lambda m: ("RX", m[1], (m[2] or "").upper())),
    (r"(?<![A-Za-z0-9])([2-5]0[5-9]0|16[356]0)\s*(TI|SUPER)(?![A-Za-z])",
     lambda m: (_nv(m[1])[0], m[1], SUFFIX[m[2].upper()])),
    (r"GV-N(\d{3,4})(T?)", lambda m: _nv(m[1] + "0" if len(m[1]) == 3 else m[1], bool(m[2]))),   # Gigabyte
    (r"GV-R(\d{4})(XT)?", lambda m: ("RX", m[1], "XT" if m[2] else "")),
    (r"ZT-[A-Z](\d{4})", lambda m: _nv(m[1])),                                                   # Zotac
    (r"(?<![A-Z0-9])NE\d(\d{3})([T0-9])", lambda m: _nv(m[1] + ("0" if m[2] == "T" else m[2]), m[2] == "T")),
    (r"(?<![A-Z])AF(1030|1050|1650|750|730|710)(?!\d)",                                          # AFOX
     lambda m: ("GTX" if m[1] in ("1050", "1650", "750") else "GT", m[1], "")),
    (r"(?<![A-Z])AF210", lambda m: ("GT", "210", "")),
    (r"AFR([579])(\d{3})", lambda m: ("R" + m[1], m[2], "")),
]
GPU_FALLBACK = [(re.compile(rx, re.I), fn) for rx, fn in GPU_FALLBACK]


def gpu_chip(title: str):
    m = CHIP.search(title)
    if m:
        pre, num = m.group(1).upper(), m.group(2).upper()
        suf = " ".join(SUFFIX[s.upper()] for s in (m.group(3), m.group(4)) if s)
        return ("Arc" if pre == "ARC" else pre), num, suf
    for rx, fn in GPU_FALLBACK:
        m = rx.search(title)
        if m:
            return fn(m)
    return None


def gpu_tier(pre: str, num: str, suf: str) -> str:
    if pre == "Arc":
        return "mid"
    if pre in ("GT", "GTX", "HD", "R5", "R7", "R9") or len(num) < 4 or not num.isdigit():
        return "old"
    if pre == "RX":
        # 9070 → «70», 7600 → «60»: у RDNA4 класс в двух последних цифрах
        lvl = int(num[2:4] if num[1] == "0" else num[1:3])
        if lvl >= 80 or (num[0] == "9" and lvl == 70):
            return "high"
        return "upper" if lvl == 70 else "mid" if lvl == 60 else "low"
    lvl = int(num[2:4])
    if lvl >= 90:
        return "flagship"
    if lvl == 80 or (lvl == 70 and "Ti" in suf):
        return "high"
    return "upper" if lvl == 70 else "mid" if lvl == 60 else "low"


# (вентиляторов, длина, высота, толщина в слотах) по умолчанию для класса
TIER_DIMS = {"flagship": (3, 340, 140, 3.5), "high": (3, 315, 132, 3), "upper": (2, 260, 122, 2.5),
             "mid": (2, 240, 115, 2), "low": (2, 200, 112, 2), "old": (1, 170, 105, 2)}
FAN_LEN = {1: 180, 2: 245, 3: 305}


def gpu_fans(t: str) -> int | None:
    t = t.upper()
    if re.search(r"(?<![A-Z0-9])(3X|X3|3 ?FAN|TRIO|TRIPLE|SUPRIM|AMP|TRINITY|JETSTREAM|GAMINGPRO)(?![A-Z0-9])|WF3"
                 r"|3 ВЕНТИЛЯТОР", t):
        return 3
    if re.search(r"(?<![A-Z0-9])(2X|X2|2 ?FAN|DUAL|TWIN|DUO|PULSE|CHALLENGER)(?![A-Z0-9])|WF2|2 ВЕНТИЛЯТОР", t):
        return 2
    if re.search(r"(?<![A-Z0-9])(1 ?FAN|SINGLE|ITX|LP|LOW PROFILE|STORMX)(?![A-Z0-9])|1 ВЕНТИЛЯТОР", t):
        return 1
    return None


def gpu_mem(t: str) -> int | None:
    m = re.search(r"(?<![\d.])(\d{1,2})\s*(?:GB|ГБ|Гб|G(?:D\d?)?)(?![A-Za-zА-Яа-я])", t)
    return int(m.group(1)) if m and int(m.group(1)) in (1, 2, 3, 4, 6, 8, 10, 11, 12, 16, 20, 24, 32) else None


def build_gpu(rows):
    """Видеокарты — по моделям нормализации (collector.normalize): одна модель
    производителя — одна позиция, сколько бы площадок её ни продавали."""
    groups, junk, unknown, carded = defaultdict(list), 0, 0, 0
    for o in rows:
        # фильтр и здесь: предложение, привязанное к модели раньше, чем фильтр узнал о подделке (CMP 30HX «аналог 1660 Super»)
        if o["model"] is None or GPU_JUNK.search(o["title"]):
            junk += 1
            continue
        groups[o["model"]].append(o)
    items = []
    for mid, offs in groups.items():
        md = offs[0]["md"]
        sp = md["specs"] or {}
        chip = gpu_chip(sp.get("chip") or "")
        if not chip:
            unknown += len(offs)
            continue
        pre, num, suf = chip
        tier = gpu_tier(pre, num, suf)
        text = " ".join(o["title"] for o in offs)
        fans_t = gpu_fans(text)
        fans, length, h, slots = TIER_DIMS[tier]
        est = ["len", "h", "slots"]
        if fans_t:
            fans = fans_t
            length = max(length, FAN_LEN[fans]) if fans == 3 else min(FAN_LEN[fans], length) if fans == 1 else length
        else:
            est.append("fans")
        m = re.search(r"(\d{3})\s*[хxХX×*]\s*(\d{2,3})\s*[хxХX×*]\s*(\d{2})(?:[.,]\d)?\s*мм", text)
        if m and 120 <= int(m.group(1)) <= 380:
            length, h = int(m.group(1)), int(m.group(2))
            slots = max(1, min(4, round(int(m.group(3)) / 20.32 * 2) / 2))
            est = [e for e in est if e == "fans"]
        # Характеристики карточек точнее и названия, и оценки по классу.
        # Слоты указаны не везде; тогда их даёт толщина. Названия полей WB и
        # DNS разные, и оси у них не совпадают: «Ширина предмета» WB — толщина,
        # а «Ширина видеокарты» DNS — высота. Порядок — DNS, Ozon, WB, ЯМ.
        thick = prop_num(offs, ("Толщина видеокарты", "Ширина предмета", "Толщина"), 15, 90)
        card = {"len": prop_num(offs, ("Длина видеокарты", "Длина, мм", "Глубина предмета", "Длина"), 120, 380),
                # от 64 мм: низкопрофильные карты — 69 мм (планка LP — 64,4 мм)
                "h": prop_num(offs, ("Ширина видеокарты", "Высота предмета", "Высота"), 64, 170),
                "slots": prop_num(offs, ("Количество занимаемых слотов расширения", "Кол-во занимаемых слотов",
                                         "Количество занимаемых слотов"), 1, 4, frac=True)
                or (thick and max(1, min(4, round(thick / 20.32 * 2) / 2))),
                "fans": prop_num(offs, ("Количество вентиляторов видеокарты",
                                        "Тип и количество установленных вентиляторов", "Количество вентиляторов"),
                                 1, 3)}
        # «Размеры, мм» Ozon и ЯМ — оси в любом порядке: длина самая большая, толщина самая малая
        sz = [s for d in prop_vals(offs, ("Размеры, мм",), dims3) if (s := sorted(d))
              and 15 <= s[0] <= 90 and 64 <= s[1] <= 170 and 120 <= s[2] <= 380]
        if sz:
            thin, mid_, big = statistics.median_low(sz)
            card["len"], card["h"] = card["len"] or round(big), card["h"] or round(mid_)
            card["slots"] = card["slots"] or max(1, min(4, round(thin / 20.32 * 2) / 2))
        length, h = card["len"] or length, card["h"] or h
        slots, fans = card["slots"] or slots, card["fans"] or fans
        est = [e for e in est if not card[e]]
        carded += any(card.values())
        # Мощность: рекомендованный производителем БП и потребление самой карты
        psu = prop_num(offs, ("Рекомендуемый блок питания", "Рекомендуемая мощность блока питания",
                              "Рекомендуемая мощность БП, Вт"), 200, 1600)
        power = prop_num(offs, ("Потребляемая мощность", "Мощность, Вт", "TDP"), 20, 700)
        items.append({"id": f"g{mid}", "name": md["name"], "article": md["article"], "chip": sp["chip"],
                      "vendor": None if md["brand"] == "н/д" else md["brand"], "mem": sp.get("mem"),
                      "color": sp.get("color"), "fans": fans, "len": length, "h": h, "slots": slots, "est": est,
                      "psu": psu, "power": power, "perf": perf.gpu_score(sp["chip"]), **summarize(offs)})
    return items, {"junk": junk, "unknown": unknown, "cards": carded}


# ---------------------------------------------------------------- корпуса

CASE_VENDORS = ["DEEPCOOL", "Zalman", "Accord", "AeroCool", "Cougar", "Montech", "Ardor Gaming", "Ocypus",
                "Chieftec", "Ginzzu", "Defender", "Bloody", "Eurocase", "CBR", "MSI", "1STPLAYER", "Lian Li",
                "NZXT", "Fractal Design", "be quiet!", "Thermaltake", "Corsair", "Cooler Master", "Phanteks",
                "HYTE", "Jonsbo", "Powercase", "ExeGate", "Formula", "Raijintek", "Xigmatek", "Hiper", "GameMax",
                "Foxline", "Prime Box", "Sama", "Darkflash", "Kingprice", "Metallic Gear", "ASUS", "GIGABYTE",
                "Thermalright", "Antec", "SilverStone", "Mars Gaming", "Crown", "Oklick", "Vinga", "Azerty",
                "Tecware", "Sunpower", "Ocean", "Gamerstorm", "Tagan", "Qube", "Sirius", "Asrock"]
CASE_SERIES_VENDOR = {"MATREXX": "DEEPCOOL", "CC560": "DEEPCOOL", "CH260": "DEEPCOOL", "CH560": "DEEPCOOL",
                      "ZM-": "Zalman", "ACC-": "Accord", "GAMMA C": "Ocypus", "DOMINION": "Ardor Gaming",
                      "MAG FORGE": "MSI", "Q500": "Cooler Master", "N80": "Jonsbo"}
CASE_JUNK = re.compile(r"для (жестк|hdd|ssd|m\.?2|телефон|ноутбук|диск)|бокс|док-станц|подставк|кронштейн|"
                       r"салазк|заглушк|пылев|фильтр|вентилятор\w* для|сетевой|шкаф|стойк\w* 19", re.I)


TPL_FORMS = {"tower": ("ATX", "mATX", "ITX"), "aquarium": ("ATX", "mATX", "ITX"), "matx": ("mATX", "ITX"),
             "itx": ("ITX",)}


def case_parse(o) -> dict:
    """Признаки корпуса из названия: шаблон геометрии, производитель, цвет, модель."""
    t = o["title"]
    low = t.lower()
    atx = bool(re.search(r"(?<!m-)(?<!micro-)(?<![a-z])(e-?)?atx(?![a-z])|midi|middle|full.?tower", low))
    matx = bool(re.search(r"(?<![a-z])m-?atx|micro-?atx|microatx|mini.?tower", low))
    itx = bool(re.search(r"mini-?itx|(?<![a-z])itx(?![a-z])|m-itx", low))
    aq = bool(re.search(r"аквариум|панорам|двухкамер|dual.?chamber|кубик|cube", low))
    tpl = "tower" if atx or not (matx or itx) else "matx" if matx else "itx"
    if aq and tpl != "itx":
        tpl = "aquarium"
    vendor = find_vendor(t, CASE_VENDORS, CASE_SERIES_VENDOR, o["brand"])
    return {"vendor": vendor, "color": color_of(t, o["wbc"]), "tpl": tpl,
            "tokens": model_tokens(t, vendor, extra_stop={"rgb", "argb", "frgb"})}


def case_forms(offs) -> list[str] | None:
    """Форм-факторы плат из карточек — самый частый набор среди предложений.
    Корпус под ATX берёт и платы меньше, а продавцы часто пишут только старший."""
    votes = Counter()
    for o in offs:
        fs = set()
        p = o["props"]
        v = (p.get("Форм-фактор материнской платы") or p.get("Форм-фактор совместимых плат")
             or p.get("Поддерживаемые форм-факторы материнской платы"))
        for t in re.split(r"[;,/]", (v or "").lower()):
            if "itx" in t:
                fs.add("ITX")
            elif re.search(r"micro|matx|m-atx", t):
                fs.add("mATX")
            elif "atx" in t:
                fs.add("ATX")
        if "ATX" in fs:
            fs |= {"mATX", "ITX"}
        if "mATX" in fs:
            fs.add("ITX")
        if fs:
            votes[frozenset(fs)] += 1
    if not votes:
        return None
    best = votes.most_common(1)[0][0]
    return [f for f in ("ATX", "mATX", "ITX") if f in best]


def build_case(rows):
    groups, junk = by_model(rows, CASE_JUNK, case_parse)
    items, carded = [], 0
    for mid, offs in groups.items():
        text = " ".join(o["title"] for o in offs).lower()
        tpl = common(o["tpl"] for o in offs)
        mg = re.search(r"(?:видеокарт\w*|vga|gpu)\D{0,15}(\d{3})\s*мм|(\d{3})\s*мм\s*(?:под\s*)?видеокарт", text)
        mc = re.search(r"(?:кулер\w*|cpu cooler)\D{0,15}(\d{2,3})\s*мм|(\d{2,3})\s*мм\s*кулер", text)
        max_gpu = int(next(g for g in mg.groups() if g)) if mg else None
        max_cool = int(next(g for g in mc.groups() if g)) if mc else None
        if max_gpu and not 150 <= max_gpu <= 500:
            max_gpu = None
        if max_cool and not 40 <= max_cool <= 200:
            max_cool = None
        cg = prop_num(offs, ("Максимальная длина видеокарты", "Максимальная длина устанавливаемой видеокарты",
                             "Макс. длина видеокарты, мм"), 150, 500)
        cc = prop_num(offs, ("Максимальная высота процессорного кулера", "Макс. высота процессорного кулера, мм"),
                      40, 200)
        max_psu = prop_num(offs, ("Максимальная длина блока питания", "Максимальная длина БП"), 100, 450)
        forms = case_forms(offs)
        max_gpu, max_cool = cg or max_gpu, cc or max_cool
        # Карточка без ATX поправляет шаблон, угаданный по названию
        if forms and "ATX" not in forms and tpl in ("tower", "aquarium"):
            tpl = "matx" if "mATX" in forms else "itx"
        # Габариты: у DNS и ЯМ поля в мм, у Ozon и WB — в см
        dims = {"H": prop_num(offs, ("Высота", "Высота, см", "Высота предмета"), 200, 650),
                "W": prop_num(offs, ("Ширина", "Ширина, см", "Ширина предмета"), 140, 330),
                "D": prop_num(offs, ("Глубина", "Глубина, см", "Глубина предмета"), 200, 650)}
        real = all(dims.values())
        carded += bool(cg or cc or forms or real)
        est = [e for e, v in (("maxGpu", max_gpu), ("maxCooler", max_cool)) if v is None] + ([] if real else ["dims"])
        items.append({"id": f"c{mid}", "name": offs[0]["md"]["name"][:80], "vendor": common(o["vendor"] for o in offs),
                      "color": common(o["color"] for o in offs), "tpl": tpl, "forms": forms or list(TPL_FORMS[tpl]),
                      "maxGpu": max_gpu, "maxCooler": max_cool, "maxPsu": max_psu, "est": est,
                      **(dims if real else {}), **summarize(offs)})
    return items, {"junk": junk, "cards": carded}


# ---------------------------------------------------------------- кулеры

COOLER_VENDORS = ["DEEPCOOL", "ID-COOLING", "Thermalright", "be quiet!", "Noctua", "Arctic", "Cooler Master",
                  "Jonsbo", "PCCooler", "Zalman", "AeroCool", "Ocypus", "Snowman", "Bloody", "Azerty", "MTOUMAN",
                  "ExeGate", "Alseye", "GameMax", "Thermaltake", "Lian Li", "Scythe", "Iceberg", "Ardor Gaming",
                  "Montech", "Endorfy", "Corsair", "NZXT", "Xilence", "Raijintek", "Formula", "Crown", "Cougar",
                  "Glacialtech", "Hiper", "Oklick", "Darkflash", "Coolmoon", "Vinga", "newpstek", "Gamerstorm",
                  "UPSIREN"]    # раньше серии ASSASSIN (Thermalright): «UPSIREN Walrus Assassin 90» — не Thermalright
COOLER_SERIES_VENDOR = {"FROZN": "ID-COOLING", "SE-2": "ID-COOLING", "IS-": "ID-COOLING", "DK-0": "ID-COOLING",
                        "AK400": "DEEPCOOL", "AK500": "DEEPCOOL", "AK620": "DEEPCOOL", "AG400": "DEEPCOOL",
                        "AG620": "DEEPCOOL", "THETA": "DEEPCOOL", "AXP-": "Thermalright", "PA120": "Thermalright",
                        "PEERLESS": "Thermalright", "PHANTOM SPIRIT": "Thermalright", "ASSASSIN": "Thermalright",
                        "CR-1": "Jonsbo", "NH-": "Noctua", "AS.01": "Alseye"}
COOLER_JUNK = re.compile(r"телефон|планшет|ноутбук|фильтр|термопаст|для корпуса|серверн|(?<![a-z0-9])[12]u(?![a-z0-9])|"
                         r"сжо|водян|жидкост|liquid|(?<![a-z])aio(?![a-z])|water|водоблок|помп|для ssd|для видеокарт|"
                         r"(secure|contact)[ -]?frame|рамк|anti-?bend",
                         re.I)
# «Dual» у кулеров чаще значит два вентилятора (Ocypus Delta A40 Elite Dual — одна башня) — только «dual tower»
DUAL = re.compile(r"dual.?tower|двухбашен|2 башн|twin tower|ak620|ag620|pa120|nh-d15|peerless|phantom spirit|fs140|"
                  r"frozn a620|dark rock pro|se-207|frost commander|royal knight", re.I)
LOW = re.compile(r"низкопрофил|low.?profile|slim|(?<![a-z])l9|(?<![a-z])l12|nh-l|is-40|is-55|is-30|axp-90|axp-100|"
                 r"(?<![a-z])dk-0|theta|(?<![a-z])box(?![a-z])|боксов", re.I)


def cooler_parse(o) -> dict:
    t = o["title"]
    vendor = find_vendor(t, COOLER_VENDORS, COOLER_SERIES_VENDOR, o["brand"])
    return {"vendor": vendor, "color": color_of(t, o["wbc"]),
            "tokens": model_tokens(t, vendor, extra_stop={"argb", "rgb", "digital"})}


def build_cooler(rows):
    groups, junk = by_model(rows, COOLER_JUNK, cooler_parse)
    items, carded = [], 0
    for mid, offs in groups.items():
        text = " ".join(o["title"] for o in offs)
        if LOW.search(text):
            kind, towers, h = "low", 1, 47 if re.search(r"axp-90|is-40|l9", text, re.I) else 58
        elif DUAL.search(text):
            kind, towers, h = "tower", 2, 158
        else:
            kind, towers, h = "tower", 1, 152
        m = re.search(r"высот\w*\D{0,6}(\d{2,3})\s*мм", text, re.I)
        est = ["h"]
        if m and 30 <= int(m.group(1)) <= 180:
            h, est = int(m.group(1)), []
        # «Высота» — поле DNS, «Высота предмета» — WB, «Высота, мм» — Ozon (последним:
        # там бывает и размер вентилятора)
        ch = prop_num(offs, ("Высота кулера", "Высота радиатора", "Высота", "Высота предмета", "Высота, мм"), 30, 180)
        if not ch:        # ЯМ: «Размеры кулера (ШхВxГ)» — высота вторая
            hs = [d[1] for d in prop_vals(offs, ("Размеры кулера (ШхВxГ)",), dims3) if 30 <= d[1] <= 180]
            ch = round(statistics.median_low(hs)) if hs else None
        if ch:
            h, est = ch, []
            carded += 1
            # Высота из карточки решает и тип: низкопрофильный или башня
            if ch < 80 and kind == "tower":
                kind, towers = "low", 1
            elif ch >= 100 and kind == "low":
                kind = "tower"
        items.append({"id": f"k{mid}", "name": offs[0]["md"]["name"][:80], "vendor": common(o["vendor"] for o in offs),
                      "color": common(o["color"] for o in offs), "type": kind, "towers": towers, "h": h, "est": est,
                      **summarize(offs)})
    return items, {"junk": junk, "cards": carded}


# ---------------------------------------------------------------- материнские платы

MB_VENDORS = ["ASRock", "ASUS", "MSI", "GIGABYTE", "Biostar", "Maxsun", "Colorful", "MACHINIST", "Huananzhi",
              "AFOX", "Esonic", "ERYING", "Jingsha", "Kllisre", "Atermiter", "Soyo", "Qiyida", "Amentmen"]
MB_SERIES_VENDOR = {"PRIME": "ASUS", "TUF": "ASUS", "ROG": "ASUS", "STRIX": "ASUS", "90MB": "ASUS",
                    "MAG ": "MSI", "MPG ": "MSI", "MEG ": "MSI", "TOMAHAWK": "MSI", "MORTAR": "MSI",
                    "PRO B": "MSI", "PRO H": "MSI", "PRO Z": "MSI", "PRO A": "MSI", "PRO X": "MSI",
                    "AORUS": "GIGABYTE", "EAGLE": "GIGABYTE", "DS3H": "GIGABYTE", "GAMING X": "GIGABYTE",
                    "STEEL LEGEND": "ASRock", "PHANTOM": "ASRock", "HDV": "ASRock", "PRO RS": "ASRock"}
MB_JUNK = re.compile(r"комплект|(?<![a-z])kit(?![a-z])|\+\s*(2\s*x\s*)?(xeon|процессор|ddr|памят|intel|amd|e5)|"
                     r"запчаст|^\W*(нов\w+\s+|безвентиляторн\w+\s+|top version\s+)?процессор|для материнск|"
                     r"серверн|supermicro|mbd-|ноутбук|для asus x|imac|кондиционер|проектор|кронштейн|"
                     r"(?<![a-z])sp5(?![a-z])|lga ?4677|lga ?2066|trx\d|str5|c7\d\d|c26\d|с процесс|встроенным процесс",
                     re.I)
# Чипсет, в том числе слитно с суффиксом: B760M-K, AB350M, A620AM-K, H610MHC
CHIPSET = re.compile(r"(?<![A-Za-z0-9])A?([ABHXZQ]\d{2,3}E?)A?(M|I)?(?=[A-Z0-9\s\-_/,.)+]|$)")
AMD_CHIPS = {"A320", "B350", "X370", "B450", "X470", "A520", "B550", "X570", "A620", "B650", "B650E", "X670",
             "X670E", "B840", "B850", "X870", "X870E"}


def mb_socket(chip: str) -> str | None:
    if chip in AMD_CHIPS:
        return "AM5" if chip[1] in "678" else "AM4"
    n = int(re.sub(r"\D", "", chip))
    if chip in ("X79",):
        return "LGA2011"
    if chip in ("X99",):
        return "LGA2011-3"
    if chip in ("X58",):
        return "LGA1366"
    if n in (81, 85, 87, 97):
        return "LGA1150"
    if n in (61, 65, 67, 68, 75, 77):
        return "LGA1155"
    if n in (55, 57):
        return "LGA1156"
    return {1: "LGA1151", 2: "LGA1151", 3: "LGA1151", 4: "LGA1200", 5: "LGA1200", 6: "LGA1700", 7: "LGA1700", 8: "LGA1851"}.get(n // 100)


def mb_parse(o) -> dict:
    """Чипсет, форм-фактор (None, если в названии не указан), тип памяти, модель."""
    t = o["title"]
    m = CHIPSET.search(re.sub(r"\[.*?\]", " ", t).upper())
    chip, suffix = (m.group(1), m.group(2)) if m else (None, None)
    low = t.lower()
    if suffix == "I" or re.search(r"mini-?itx|(?<![a-z])itx(?![a-z])", low):
        form = "ITX"
    elif suffix == "M" or re.search(r"(?<![a-z])m-?atx|micro-?atx|microatx", low):
        form = "mATX"
    elif re.search(r"(?<!m-)(?<!micro-)(?<![a-z])(e-?)?atx(?![a-z])", low):
        form = "ATX"
    else:
        form = None
    mem = "DDR5" if re.search(r"ddr5|(?<![a-z])d5(?![a-z0-9])", low) else \
          "DDR4" if re.search(r"ddr4|(?<![a-z])d4(?![a-z0-9])", low) else None
    vendor = find_vendor(t, MB_VENDORS, MB_SERIES_VENDOR, o["brand"])
    toks = [x for x in model_tokens(re.sub(r"\[.*?\]", " ", t), vendor, extra_stop={"wi-fi"})
            if not re.fullmatch(r"(lga|am|socket)\S*|\d{3,4}", x.lower())]
    return {"vendor": vendor, "chip": chip, "form": form, "mem": mem, "color": color_of(t, o["wbc"]), "tokens": toks}


def mb_card(offs) -> dict:
    """Характеристики платы из карточек WB и DNS: память, накопители, слоты, Wi-Fi."""
    def ddr(v):
        m = re.search(r"ddr\s*-?([345])", v, re.I)
        return f"DDR{m.group(1)}" if m else None

    def form(v):
        t = v.lower()
        return "ITX" if "itx" in t else "mATX" if re.search(r"micro|matx|m-atx", t) else "ATX" if "atx" in t else None

    def mhz(v):
        # У DNS разгонные частоты — списком, берётся наибольшая
        xs = [int(x) for x in re.findall(r"(?<!\d)(\d{4,5})(?!\d)", v) if 1000 <= int(x) <= 12000]
        return max(xs) if xs else None

    def wifi(v):
        m = re.search(r"(?<![\d.])([4-7]E?)\s*\(802\.11", v)
        if m:
            return m.group(1)
        m = re.search(r"802\.11\s*(be|ax|ac)", v, re.I)
        return {"be": "7", "ax": "6", "ac": "5"}[m.group(1).lower()] if m else None

    def x16(v):
        # «1 x PCIe 4.0 (в режиме x16), 1 x PCIe 3.0 (в режиме x4)» или просто «2»
        xs = re.findall(r"(\d)\s*[xх×]\s*PCI", v, re.I)
        if xs:
            return sum(map(int, xs))
        m = re.fullmatch(r"(\d)\s*(?:шт\.?)?", v.strip())
        return int(m.group(1)) if m else None

    def pcie(v):
        m = re.fullmatch(r"([2-5])(?:\.0)?", v.strip())
        return m.group(1) + ".0" if m else None

    def top(names, parse):
        return common(prop_vals(offs, names, parse))

    return {
        "form": top(("Форм-фактор", "Форм-фактор материнской платы"), form),
        # Порядок названий — DNS, WB, Ozon
        "mem": top(("Тип поддерживаемой памяти", "Тип памяти материнской платы", "Тип памяти"), ddr),
        "memSlots": prop_num(offs, ("Количество слотов памяти", "Количество слотов оперативной памяти",
                                    "Количество слотов RAM"), 1, 8),
        "memMax": prop_num(offs, ("Максимальный объем памяти", "Максимальный объём памяти"), 8, 1024),
        "memFreq": top(("Максимальная частота памяти (JEDEC / без разгона)",), mhz),
        "memOc": top(("Частота оперативной памяти в разгоне", "Максимальная частота оперативной памяти",
                      "Макс. поддерживаемая частота RAM", "Максимальная частота памяти"), mhz),
        "m2": prop_num(offs, ("Количество разъемов M.2", "Количество слотов M.2"), 0, 6),
        "sata": prop_num(offs, ("Количество портов SATA", "Количество разъемов SATA",
                                "Кол-во внутренних разъемов SATA 6 Гбит/с"), 0, 12),
        "x16": top(("Слоты PCIe x16", "Количество слотов PCI-E x16", "Количество PCI-E x16"), x16),
        "pcie": top(("Версия PCI Express",), pcie),
        "wifi": top(("Стандарт Wi-Fi", "Беспроводные интерфейсы"), wifi),
    }


def build_mb(rows):
    groups, junk = by_model(rows, MB_JUNK, mb_parse)
    items, unknown, carded = [], 0, 0
    for mid, offs in groups.items():
        chip = common(o["chip"] for o in offs)
        if not chip:
            unknown += len(offs)
            continue
        card = mb_card(offs)
        carded += any(v is not None for v in card.values())
        # Форм-фактор и тип памяти из карточки надёжнее угаданных по названию
        form, mem = card.pop("form"), card.pop("mem")
        socket = mb_socket(chip)
        if socket in ("AM5", "LGA1851"):    # платформы только под DDR5; «DDR4» в названии — ошибка продавца
            mem = "DDR5"
        items.append({"id": f"m{mid}", "name": offs[0]["md"]["name"][:80], "vendor": common(o["vendor"] for o in offs),
                      "chipset": chip, "form": form or common(o["form"] for o in offs) or "ATX",
                      "socket": socket, "mem": mem or common(o["mem"] for o in offs), "color": None,
                      **card, **summarize(offs)})
    return items, {"junk": junk, "unknown": unknown, "cards": carded}


# ---------------------------------------------------------------- накопители SSD

SSD_VENDORS = ["Samsung", "Kingston", "Crucial", "WD", "Western Digital", "ADATA", "Apacer", "Netac", "Patriot",
               "Silicon Power", "TEAMGROUP", "Team Group", "Transcend", "Digma", "KingSpec", "Colorful", "GIGABYTE",
               "MSI", "Seagate", "SanDisk", "Kioxia", "Toshiba", "Hikvision", "HIKSEMI", "Lexar", "Solidigm", "Intel",
               "Goodram", "Acer", "HP", "Biwin", "Smartbuy", "Foxline", "Qumo", "ExeGate", "Kimtigo", "Walram",
               "Dahua", "SK hynix", "Micron", "Ardor Gaming", "AGI", "Fanxiang", "Maxsun", "Corsair", "PNY", "Sabrent",
               "Mirex", "Goldenfir", "Reeinno", "Thermaltake", "AMD", "Aceline", "Gamerking", "Ocypus", "Geil"]
SSD_SERIES_VENDOR = {"SNV": "Kingston", "SA400": "Kingston", "KC3000": "Kingston", "SKC": "Kingston", "FURY": "Kingston",
                     "MZ-": "Samsung", "EVO": "Samsung", "CT": "Crucial", "WDS": "WD", "SLEG": "ADATA", "ALEG": "ADATA",
                     "LEGEND": "ADATA", "AS2280": "Apacer", "AS340": "Apacer", "AS350": "Apacer", "NT01": "Netac",
                     "N535S": "Netac", "SA500": "Netac", "N930": "Netac", "P210": "Patriot", "P220": "Patriot",
                     "SP0": "Silicon Power", "SP1": "Silicon Power", "SP2": "Silicon Power", "SP5": "Silicon Power",
                     "T253": "TEAMGROUP", "DGS": "Digma", "SMAR-": "KingSpec"}
SSD_JUNK = re.compile(r"внешн|portable|портатив|usb|type-?c|бокс|корпус для|адаптер|переходник|радиатор|кабел|шлейф|"
                      r"салазк|m-?sata|mini ?sata|(?<![a-z])sas(?![a-z])|u\.2|флеш|карт\w* памяти", re.I)
SSD_CAPS = {120, 128, 240, 250, 256, 480, 500, 512, 960, 1000, 1024, 2000, 2048, 4000, 4096, 8000, 8192}
SSD_STOP = {"ssd", "m.2", "m2", "nvme", "pcie", "pci-e", "pci", "sata", "sata3", "sata-iii", "sataiii", "iii", "2.5",
            "2280", "2242", "2230", "tlc", "qlc", "mlc", "nand", "3d", "internal", "tb", "gb", "oem", "retail",
            "series", "disk", "drive", "solid", "state", "gen3", "gen4", "gen5", "x4"}


def ssd_cap(t: str) -> int | None:
    """Объём в гигабайтах. Несколько разных объёмов в одном названии —
    карточка-витрина «120/256/512 ГБ», модель по ней не определить."""
    found = set()
    for m in re.finditer(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(тб|tb|гб|gb|t|g)(?![a-zа-я])", t, re.I):
        v, u = float(m[1].replace(",", ".")), m[2].lower()
        gb = round(v * 1000) if u in ("тб", "tb", "t") else round(v)
        if gb in SSD_CAPS:
            found.add(gb)
    return found.pop() if len(found) == 1 else None


def ssd_kind(t: str) -> tuple[str | None, str | None]:
    """Форма (M.2 или 2.5) и интерфейс по названию."""
    low = t.lower()
    nvme = bool(re.search(r"nvme|pci-?e|pcie", low))
    m2 = bool(re.search(r"(?<![a-z0-9])m\.?2(?![0-9])|22(80|42|30)", low))
    gen = re.search(r"(?:pci-?e|pcie|gen)\s*(\d)(?![0-9])", low)
    form = "M.2" if m2 or nvme else "2.5" if re.search(r"2[.,]5|sata", low) else None
    iface = (f"NVMe PCIe {gen[1]}.0" if gen else "NVMe") if nvme else "SATA III" if "sata" in low or form == "2.5" else None
    return form, iface


def cap_label(gb: int) -> str:
    return f"{round(gb / 1000, 1):g} ТБ".replace(".", ",") if gb >= 1000 else f"{gb} ГБ"


def ssd_parse(o) -> dict:
    t = o["title"]
    vendor = find_vendor(t, SSD_VENDORS, SSD_SERIES_VENDOR, o["brand"])
    form, iface = ssd_kind(t)
    toks = [x for x in model_tokens(re.sub(r"\[.*?\]|\(.*?\)", " ", t), vendor, extra_stop=SSD_STOP)
            if not re.fullmatch(r"\d+(gb|tb|g|t)|\d[.,]\d|gen\d|x\d|pcie?\d.*|"
                                r"(120|128|240|250|256|480|500|512|960|1000|1024|2000|2048|4000|4096|8000)", x.lower())
            and not re.search(r"[а-яё]", x.lower())]                       # «SSD-диск» — не модель
    return {"vendor": vendor, "cap": ssd_cap(t), "form": form, "iface": iface, "tokens": toks}


def build_ssd(rows):
    groups, junk = by_model(rows, SSD_JUNK, ssd_parse)
    items, unknown = [], 0
    for mid, offs in groups.items():
        cap, form = common(o["cap"] for o in offs), common(o["form"] for o in offs)
        if not cap or not form:
            unknown += len(offs)
            continue
        ifaces = Counter(o["iface"] for o in offs if o["iface"])
        # точнее — с поколением PCIe; из равных — самый частый
        iface = max(ifaces, key=lambda v: ("PCIe" in v, ifaces[v])) if ifaces else None
        items.append({"id": f"s{mid}", "name": offs[0]["md"]["name"][:90], "vendor": common(o["vendor"] for o in offs),
                      "cap": cap, "form": form, "iface": iface, "color": None, **summarize(offs)})
    return items, {"junk": junk, "unknown": unknown}


# ---------------------------------------------------------------- жёсткие диски HDD

HDD_VENDORS = ["Seagate", "WD", "Western Digital", "Toshiba", "HGST", "Hitachi", "Synology", "Dahua", "Hikvision"]
HDD_SERIES_VENDOR = {"BARRACUDA": "Seagate", "IRONWOLF": "Seagate", "SKYHAWK": "Seagate", "EXOS": "Seagate",
                     **{f"ST{d}": "Seagate" for d in "12345689"}, "WD": "WD", "HDW": "Toshiba", "MG0": "Toshiba", "MG1": "Toshiba", "WUH": "WD", "WUS": "WD", "HUS": "HGST", "HUH": "HGST"}
# Только 3,5″ для ПК: ноутбучные 2,5″, внешние, серверные SAS, б/у и восстановленные — мимо
HDD_JUNK = re.compile(r"внешн|portable|портатив|usb|type-?c|бокс|корпус для|адаптер|переходник|кабел|шлейф|салазк|"
                      r"ноутбук|laptop|2[.,]5\s*(\"|″|''|дюйм|inch)|(?<![a-z])sas(?![a-z])|(?<![a-z])ssd(?![a-z])|"
                      r"б/у|восстановлен|refurb|(?<![a-z])used(?![a-z])|флеш|карт\w* памяти", re.I)
HDD_CAPS = {160, 250, 320, 500, 750, 1000, 2000, 3000, 4000, 6000, 8000, 10000, 12000, 14000, 16000, 18000, 20000,
            22000, 24000, 26000, 28000, 30000, 32000}
HDD_STOP = {"hdd", "sata", "sata3", "sata-iii", "sataiii", "iii", "3.5", "tb", "gb", "oem", "retail", "internal",
            "hard", "disk", "drive", "rpm", "cache", "mb", "6gb/s", "6gbps", "256mb", "128mb", "64mb", "512mb"}


def hdd_art_cap(t: str) -> int | None:
    """Объём по артикулу, когда в названии его нет: ST20000NM002C — 20000 ГБ,
    WD60PURZ — 6 ТБ, WD101EFBX — 10, WD8002 — 8, MG09ACA18TE — 18, MG10ADA800E — 8,
    WUH722222 — 22 (две последние цифры группы)."""
    up = t.upper()
    if m := re.search(r"(?<![A-Z0-9])ST(\d{3,5})[A-Z]{2}\d", up):
        return int(m[1])
    if m := re.search(r"(?<![A-Z0-9])WD(\d{2,4})[A-Z]{3}", up):
        d = m[1]
        tb = 0.5 if d == "5000" else int(d[0]) if len(d) == 4 else int(d[:2]) / (10 if len(d) == 2 else 1)
        return round(tb * 1000)
    if m := re.search(r"(?<![A-Z0-9])MG\d{2}A[CD]A(\d{2,3})", up):
        return int(m[1]) * (1000 if len(m[1]) == 2 else 10)
    if m := re.search(r"(?<![A-Z0-9])(?:HU|WU)[HS]7\d{3}(\d{2})", up):
        return int(m[1]) * 1000
    return None


def hdd_cap(t: str) -> int | None:
    """Объём в гигабайтах; как у SSD, витрина из нескольких объёмов — не модель.
    Единицы бывают в смешанной раскладке: «1TБ», «1 ТB»."""
    found = set()
    for m in re.finditer(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(терабайт\w*|гигабайт\w*|[тt][бb]|[гg][бb]|t|g)(?![a-zа-я])",
                         t, re.I):
        v, u = float(m[1].replace(",", ".")), m[2].lower()
        gb = round(v * 1000) if u[0] in "тt" else round(v)
        if gb in HDD_CAPS:
            found.add(gb)
    if not found and (gb := hdd_art_cap(t)) in HDD_CAPS:
        found.add(gb)
    return found.pop() if len(found) == 1 else None


def hdd_rpm(t: str) -> int | None:
    m = re.search(r"(?<!\d)(5400|5640|5700|5900|7200)(?!\d)|(?<![a-z])7k2(?![0-9])", t, re.I)
    return (int(m[1]) if m[1] else 7200) if m else None


def hdd_parse(o) -> dict:
    t = o["title"]
    vendor = find_vendor(t, HDD_VENDORS, HDD_SERIES_VENDOR, o["brand"])
    if vendor == "Western Digital":
        vendor = "WD"
    toks = [x for x in model_tokens(re.sub(r"\[.*?\]|\(.*?\)", " ", t), vendor, extra_stop=HDD_STOP)
            if not re.fullmatch(r"\d+(gb|tb|g|t|mb)|\d[.,]\d|\d{3,5}(rpm)?|\d+", x.lower())
            and not re.search(r"[а-яё]", x.lower())]
    return {"vendor": vendor, "cap": hdd_cap(t), "rpm": hdd_rpm(t), "form": "3.5", "tokens": toks}


def build_hdd(rows):
    groups, junk = by_model(rows, HDD_JUNK, hdd_parse)
    items, unknown = [], 0
    for mid, offs in groups.items():
        cap = common(o["cap"] for o in offs)
        if not cap:
            unknown += len(offs)
            continue
        items.append({"id": f"h{mid}", "name": offs[0]["md"]["name"][:90], "vendor": common(o["vendor"] for o in offs),
                      "cap": cap, "rpm": common(o["rpm"] for o in offs), "form": "3.5", "iface": "SATA III",
                      "color": None, **summarize(offs)})
    return items, {"junk": junk, "unknown": unknown}


# ---------------------------------------------------------------- процессоры

CPU_JUNK = re.compile(r"ноутбук|комплект|(?<![a-z])kit(?![a-z])|\+\s*(материнск|плат|памят|ddr|кулер|mb)|"
                      r"термопаст|кулер для|вентилятор для|переходник|скальпир|делидер|(?<![a-z])bga|pga ?988|"
                      r"(?<![a-z])(?:socket|сокет) для", re.I)
# Модель процессора — главный признак: артикула производителя в названиях нет
CPU_MODELS = [(re.compile(rx, re.I), fn) for rx, fn in (
    (r"ryzen\s*([3579])\s*(?:pro\s*)?(\d{4})\s*(x3d|xt|x|gt|ge|g|f)?(?![a-z0-9])",
     lambda m: ("AMD", f"Ryzen {m[1]} {m[2]}{(m[3] or '').upper()}")),
    (r"athlon\s*(?:gold\s*|silver\s*)?(\d{3,4})\s*(ge|g)(?![a-z0-9])",
     lambda m: ("AMD", f"Athlon {m[1]}{m[2].upper()}")),
    (r"core\s*ultra\s*([3579])\s*-?\s*(\d{3})\s*(kf|k|f|t)?(?![a-z0-9])",
     lambda m: ("Intel", f"Core Ultra {m[1]} {m[2]}{(m[3] or '').upper()}")),
    # Ноутбучные суффиксы (M, U, H…) распознаются, чтобы такие процессоры отсеять
    (r"(?<![a-z0-9])i([3579])\s*-?\s*(\d{4,5})\s*(kf|ks|k|f|te|t|s|hq|hk|hx|mq|m|u|h)?(?![a-z0-9])",
     lambda m: ("Intel", f"Core i{m[1]}-{m[2]}{(m[3] or '').upper()}")),
    (r"xeon\s*(e[357])\s*-?\s*(\d{4})\s*(v\d)?(?![a-z0-9])",
     lambda m: ("Intel", f"Xeon {m[1].upper()}-{m[2]}" + (f" {m[3].lower()}" if m[3] else ""))),
    (r"(pentium|celeron)\s*(?:gold\s*)?(g\d{4})\s*(t|e)?(?![a-z0-9])",
     lambda m: ("Intel", f"{m[1].title()} {m[2].upper()}{(m[3] or '').upper()}")),
)]
CPU_MOBILE = re.compile(r"-\d{4,5}(HQ|HK|HX|MQ|M|U|H)$")


def cpu_model(t: str):
    """(производитель, модель). Витрина «i3-10100 / i5-10400 / i7-10700» —
    несколько моделей в одном названии — не определяется."""
    found = {fn(m) for rx, fn in CPU_MODELS for m in rx.finditer(t)}
    return found.pop() if len(found) == 1 else None


def cpu_socket(model: str) -> str | None:
    """Сокет по модели — в тех же обозначениях, что у плат (mb_socket)."""
    if m := re.match(r"Ryzen \d (\d)", model):
        return "AM5" if m[1] in "789" else "AM4"
    if model.startswith("Athlon"):
        return "AM4"
    if model.startswith("Core Ultra"):
        return "LGA1851"
    if m := re.match(r"Core i([79])-(\d)([89])\d\d([A-Z]*)$", model):
        # HEDT: i7-3930K/4960X — LGA2011, 5930K/6850K — LGA2011-3, 7800X/9900X — LGA2066 (i9-9900K — обычный)
        gen, x = int(m[2]), m[4].startswith("X")
        if gen in (3, 4):
            return "LGA2011"
        if gen in (5, 6):
            return "LGA2011-3"
        if gen == 7 or gen == 9 and x:
            return "LGA2066"
    if m := re.match(r"Core i\d-(\d+)", model):
        gen = int(m[1][:2]) if len(m[1]) == 5 else int(m[1][0])
        return {2: "LGA1155", 3: "LGA1155", 4: "LGA1150", 5: "LGA1150", 6: "LGA1151", 7: "LGA1151", 8: "LGA1151",
                9: "LGA1151", 10: "LGA1200", 11: "LGA1200", 12: "LGA1700", 13: "LGA1700", 14: "LGA1700"}.get(gen)
    if m := re.match(r"Xeon E5-\d{4}(?: v(\d))?", model):
        return "LGA2011-3" if m[1] in ("3", "4") else "LGA2011"
    if m := re.match(r"(?:Pentium|Celeron) G(\d)(\d)", model):
        a = int(m[1])
        if a == 3 and m[2] == "9":          # Celeron G3900/G3930 — уже LGA1151
            return "LGA1151"
        if a == 5:                          # G5400 — LGA1151, G5905 — LGA1200
            return "LGA1200" if m[2] == "9" else "LGA1151"
        return {1: "LGA1155", 2: "LGA1155", 3: "LGA1150", 4: "LGA1151", 6: "LGA1200", 7: "LGA1700"}.get(a)
    return None


def cpu_igpu(model: str) -> bool:
    """Есть ли встроенная графика: без неё сборке обязательна видеокарта."""
    m = re.search(r"(\d{3,5})([A-Z0-9]*)$", model.split(" v")[0])
    if model.startswith("Xeon") or not m:
        return False
    num, suf = m[1], m[2]
    if model.startswith(("Ryzen", "Athlon")):
        # У Ryzen 7000 и новее графика есть во всех, кроме F; у старших серий — только G
        return "G" in suf or (model.startswith("Ryzen") and num[0] in "789" and "F" not in suf)
    return "F" not in suf


def cpu_parse(o) -> dict:
    t = re.sub(r"\[.*?\]", " ", o["title"])
    low = t.lower()
    vendor, model = cpu_model(t) or (None, None)
    if model and CPU_MOBILE.search(model):
        vendor = model = None
    pack = "BOX" if re.search(r"(?<![a-z])box(?![a-z])|боксов", low) else \
        "OEM" if re.search(r"(?<![a-z])(oem|tray)(?![a-z])|без кулера", low) else None
    return {"vendor": vendor, "cpu": model, "pack": pack, "tokens": []}


def build_cpu(rows):
    groups, junk = by_model(rows, CPU_JUNK, cpu_parse)
    # Нормализация иногда делит один процессор на несколько моделей (OEM, без пометки, BOX):
    # сливаем по модели; BOX — отдельно (другая комплектация), без пометки — к OEM
    merged = {}
    for mid, offs in sorted(groups.items(), key=lambda g: -len(g[1])):
        model = common(o["cpu"] for o in offs)
        merge_into(merged, (model, common(o["pack"] for o in offs) == "BOX") if model else mid, mid, offs)
    groups = dict(merged.values())
    items, unknown = [], 0
    for mid, offs in groups.items():
        model = common(o["cpu"] for o in offs)
        if not model:
            unknown += len(offs)
            continue
        # Сокет — по модели (таблица надёжнее продавцов), карточка — запасной путь
        socket = cpu_socket(model) or common(prop_vals(offs, ("Сокет", "Сокет процессора"), lambda v: (
            m := re.fullmatch(r"(AM[45]|LGA)\s*-?\s*(\d{4}(?:-3)?)?", v.strip(), re.I)) and
            (m[1].upper() + (m[2] or "")) or None))
        items.append({"id": f"p{mid}", "name": offs[0]["md"]["name"][:80], "vendor": common(o["vendor"] for o in offs),
                      "model": model, "socket": socket, "igpu": cpu_igpu(model),
                      "pack": common(o["pack"] for o in offs),
                      "tdp": prop_num(offs, ("Тепловыделение (TDP)", "Базовое тепловыделение", "Тепловыделение",
                                             "Тепловыделение, Вт", "TDP"), 10, 400),
                      "cores": prop_num(offs, ("Общее количество ядер", "Количество ядер процессора",
                                               "Количество ядер"), 1, 128),
                      "threads": prop_num(offs, ("Максимальное число потоков", "Количество потоков"), 1, 256),
                      "ghz": prop_num(offs, ("Максимальная частота процессора в разгоне",
                                             "Максимальная частота в турбо-режиме", "Частота в режиме Turbo",
                                             "Максимальная частота в турбо режиме",   # DNS
                                             "Турбо-частота, ГГц"), 1, 7, frac=True),
                      **summarize(offs)})
        it = items[-1]
        it["perf"], it["mt"] = perf.cpu_score(model, it["cores"], it["threads"], it["ghz"]) or (None, None)
    return items, {"junk": junk, "unknown": unknown}


# ---------------------------------------------------------------- оперативная память

RAM_VENDORS = ["Kingston", "ADATA", "Corsair", "G.Skill", "Crucial", "Samsung", "SK hynix", "Hynix", "Patriot",
               "TEAMGROUP", "Team Group", "Netac", "Digma", "Apacer", "Goodram", "Silicon Power", "KingSpec",
               "Atermiter", "Foxline", "AMD", "GeIL", "Qumo", "Transcend", "HIKSEMI", "Hikvision", "Lexar", "Micron",
               "PNY", "Thermaltake", "Biwin", "Asgard", "Walram", "Ardor Gaming", "Acer", "GIGABYTE", "KingBank",
               "Fanxiang", "ExeGate", "Ramaxel", "Elpida", "Colorful", "Maxsun", "Juhor", "Kllisre", "Gloway",
               "Neo Forza", "Mushkin", "Smartbuy", "AFOX", "Aceline", "Kimtigo", "Orric", "Zhiqi", "CUSU", "AGI"]
RAM_SERIES_VENDOR = {"KF3": "Kingston", "KF4": "Kingston", "KF5": "Kingston", "KVR": "Kingston", "FURY": "Kingston",
                     "AX4U": "ADATA", "AX5U": "ADATA", "AD4U": "ADATA", "AD5U": "ADATA", "XPG": "ADATA",
                     "LANCER": "ADATA", "SPECTRIX": "ADATA", "GAMMIX": "ADATA", "CMK": "Corsair", "CMH": "Corsair",
                     "CMW": "Corsair", "CMT": "Corsair", "VENGEANCE": "Corsair", "F4-": "G.Skill", "F5-": "G.Skill",
                     "TRIDENT": "G.Skill", "RIPJAWS": "G.Skill", "PVE": "Patriot", "PSD": "Patriot", "PVB": "Patriot",
                     "VIPER": "Patriot", "NTSD": "Netac", "NTBS": "Netac", "NTSH": "Netac", "DGMA": "Digma",
                     "M378": "Samsung", "M323": "Samsung", "HMA": "SK hynix", "T-FORCE": "TEAMGROUP"}
RAM_JUNK = re.compile(r"so-?dimm|sodimm|ноутбук|notebook|laptop|(?<![a-z])ecc(?![a-z])|(?<![a-z])reg(?![a-z])|"
                      r"registered|rdimm|lrdimm|сервер|server|ddr\s*[12](?!\d)|радиатор|охлажд|подставк|тестер|"
                      r"кулер|флеш|карт\w* памяти|micro ?sd|(?<![a-z])usb|(?<![a-z])ssd|видеокарт|моноблок|imac|"
                      r"macbook", re.I)
RAM_CAPS = {2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256}
RAM_FREQS = [1066, 1333, 1600, 1866, 2133, 2400, 2666, 2933, 3000, 3200, 3333, 3466, 3600, 3733, 3800, 4000, 4133,
             4266, 4400, 4600, 4800, 5200, 5333, 5600, 6000, 6200, 6400, 6600, 6800, 7000, 7200, 7600, 8000, 8200,
             8400, 8800]
# Тип и частота по артикулу производителя: у DNS в названии их нет — KF432C16 → DDR4-3200
RAM_ART = [(re.compile(rx, re.I), fn) for rx, fn in (
    (r"(?<![A-Z0-9])(?:KF|KVR)([345])(\d{2})", lambda m: (m[1], int(m[2]) * 100)),
    (r"(?<![A-Z0-9])A[XD]([45])U(\d{4})", lambda m: (m[1], int(m[2]))),
    (r"(?<![A-Z0-9])F([45])-(\d{4})", lambda m: (m[1], int(m[2]))),
    (r"(?<![A-Z0-9])NT\w{2,4}D([45])P(\d{2})", lambda m: (m[1], int(m[2]) * 100)),
)]
RAM_TOK_JUNK = re.compile(r"\d+(mhz|мгц|gb|гб|g|mb|мб)|cl\d+|pc\d.*|\d+x\d+.*|x\d|u?dimm|rgb|argb|ddr|озу|1\.\d+v?|"
                          r"\d{3,5}|kit", re.I)


def ram_freq(f) -> int | None:
    """Частота к стандартному ряду: PC4-21300 / 8 = 2662 → 2666."""
    if not f:
        return None
    best = min(RAM_FREQS, key=lambda x: abs(x - f))
    return best if abs(best - f) <= best * 0.02 else None


def ram_parse(o) -> dict:
    t = o["title"]
    low = t.lower()
    ddr = freq = None
    if m := re.search(r"ddr\s*-?\s*([345])(?!\d)", low) or re.search(r"(?<![a-z])pc([345])-?\d{4,5}", low):
        ddr = "DDR" + m[1]
    for rx, fn in RAM_ART:
        if m := rx.search(t):
            d, freq = fn(m)
            ddr = ddr or "DDR" + d
            break
    if m := re.search(r"(?<!\d)(\d{4})\s*(?:mhz|мгц|mt/s|мт/с)", low) or re.search(r"ddr[345]\s*-\s*(\d{4})(?!\d)", low):
        freq = int(m[1])
    elif not freq and (m := re.search(r"pc[345]\s*-?\s*(\d{5})", low)):
        freq = int(m[1]) / 8
    freq = ram_freq(freq)
    # Комплект: «2x16 ГБ», «16GBx2», «2 шт по 8 ГБ», у Kingston — «K2/32» в артикуле
    kit = per = cap = None
    for rx, order in ((r"(?<![\d.])([1-8])\s*[xх×*]\s*(\d{1,3})\s*(?:gb|гб|g)(?![a-zа-я])", 1),
                      (r"(?<![\d.])(\d{1,3})\s*(?:gb|гб|g)\s*[xх×*]\s*([1-8])(?!\d)", -1),
                      (r"(?<!\d)([1-8])\s*шт\w*\s*по\s*(\d{1,3})\s*(?:gb|гб)", 1)):
        if m := re.search(rx, low):
            kit, per = (int(x) for x in (m[1], m[2])[::order])
            cap = kit * per
            break
    if m := re.search(r"K([248])/(\d{2,3})", t):
        kit, cap = int(m[1]), int(m[2])
    if cap is None and not re.search(r"\d+\s*(?:gb|гб|g)?\s*/\s*\d+\s*(?:gb|гб|g)", low):   # не витрина «16/32 ГБ»
        caps = {int(x) for x in re.findall(r"(?<![\d.,/])(\d{1,3})\s*(?:gb|гб|g)(?![a-zа-я])", low)}
        cap = caps.pop() if len(caps) == 1 else None
    # Объём модуля в артикуле ADATA: AX5U5600C4616G, AX4U32008G16A
    if cap and not kit and (m := re.search(r"A[XD][45]U\d{4}(?:C\d{2}(\d{1,2})G|(\d{1,2})G\d{2})", t)):
        per = int(m[1] or m[2])
        kit = cap // per if cap % per == 0 else None
    if cap not in RAM_CAPS:
        cap = None
    vendor = find_vendor(t, RAM_VENDORS, RAM_SERIES_VENDOR, o["brand"])
    toks = [x for x in model_tokens(t, vendor) if not RAM_TOK_JUNK.fullmatch(x)]
    return {"vendor": vendor, "type": ddr, "cap": cap, "kit": kit, "freq": freq, "color": color_of(t, o["wbc"]),
            "tokens": toks}


def build_ram(rows):
    groups, junk = by_model(rows, RAM_JUNK, ram_parse)
    items, unknown = [], 0
    for mid, offs in groups.items():
        cap = common(o["cap"] for o in offs)
        if not cap:
            unknown += len(offs)
            continue
        # Название — главный источник (по нему шла нормализация), карточка дополняет пропуски
        ddr = common(o["type"] for o in offs) or common(prop_vals(
            offs, ("Тип оперативной памяти", "Тип памяти"),
            lambda v: (m := re.search(r"ddr\s*([345])", v, re.I)) and "DDR" + m[1] or None))
        kit = common(o["kit"] for o in offs) or prop_num(offs, ("Количество модулей в комплекте",
                                                                "Количество модулей"), 1, 8)
        freq = common(o["freq"] for o in offs) or ram_freq(prop_num(offs, ("Частота оперативной памяти",
                                                                           "Тактовая частота, МГц",
                                                                           "Тактовая частота"), 1000, 9000))
        items.append({"id": f"r{mid}", "name": offs[0]["md"]["name"][:90], "vendor": common(o["vendor"] for o in offs),
                      "type": ddr, "cap": cap, "kit": kit, "freq": freq, "color": common(o["color"] for o in offs),
                      "cl": prop_num(offs, ("CAS Latency (CL)", "CAS Latency (CL), тактов", "Тайминги CAS Latency (CL)",
                                            "CAS Latency"), 8, 60),
                      **summarize(offs)})
    return items, {"junk": junk, "unknown": unknown}


# ---------------------------------------------------------------- блоки питания

PSU_VENDORS = ["DEEPCOOL", "Cougar", "Thermaltake", "PCCooler", "1STPLAYER", "Azerty", "ExeGate", "Super Flower",
               "SAMA", "Ocypus", "Ginzzu", "Chieftec", "Corsair", "be quiet!", "Seasonic", "Cooler Master", "MSI",
               "ASUS", "GIGABYTE", "Zalman", "AeroCool", "FSP", "ADATA", "Montech", "Formula", "Powerman", "Hiper",
               "Crown", "Accord", "Foxline", "Qdion", "Raijintek", "Fractal Design", "NZXT", "Lian Li", "Enermax",
               "SilverStone", "GameMax", "Ardor Gaming", "Bloody", "Digma", "Xilence", "Sirius", "Kingprice", "CBR",
               "InWin", "T.F.SKYWINDINTL", "Magnetar", "DEXP", "Linkworld", "HONGLONG", "Segotep", "Huntkey", "AFOX",
               "Aceline", "Gamerstorm"]
PSU_SERIES_VENDOR = {"R-P": "DEEPCOOL", "GAMERSTORM": "DEEPCOOL", "RPL-": "Azerty", "RPB-": "Azerty", "EX2": "ExeGate",
                     "HA-": "1STPLAYER", "NGDP": "1STPLAYER", "SSR-": "Seasonic", "MPE-": "Cooler Master",
                     "MWE": "Cooler Master", "CP-90": "Corsair", "XPG": "ADATA", "PS-STP": "Thermaltake",
                     "TOUGHPOWER": "Thermaltake", "LEADEX": "Super Flower", "31GR": "Cougar", "HAT-": "SAMA"}
PSU_JUNK = re.compile(r"светодиод|(?<![a-z])led(?![a-z])|для\s+(лент|камер|видеонаблюд|роутер|ноутбук|монитор|принтер|"
                      r"сервер|моноблок|майнинг|asic|асик)|ноутбук|адаптер|зарядн|^\W*кабел|кабель\s+(питания|для)|"
                      r"переходник|удлинит|тестер|разветвит|dc-dc|(?<![a-z])din(?![a-z])|poe|ибп|бесперебой|"
                      r"стабилизатор|power ?bank|повербанк|б/у|б\.у\.|(?<![a-z0-9])(12|24|48)\s*(v|в)(?![0-9a-zа-я-])|"
                      r"(?<![a-z])(tfx|flex|1u|2u)(?![a-z])|серверн|^\W*корпус|майнинг", re.I)
PSU_TOK_JUNK = re.compile(r"80\+?|plus|white|bronze|silver|gold|platinum|titanium|atx\S*|gen\.?\d*|pcie|\d\.\d|"
                          r"12v\S*|a?pfc|modular|full|semi|fan|\d+mm|rtl|oem|cab|\d{3,4}w?", re.I)


def psu_parse(o) -> dict:
    t = o["title"]
    low = re.sub(r"\[.*?\]", " ", t.lower())
    showcase = re.search(r"\d{3}\s*/\s*\d{3}", low)                   # «500/600/700 Вт»
    ws = {int(x) for x in re.findall(r"(?<![\d.])(\d{3,4})\s*(?:w|вт|ватт|watt)(?![a-zа-я])", low)}
    if not ws:
        # Мощность в имени модели: PF750, SFX1000, CB600, «GR 850»
        ws = {int(x) for x in re.findall(r"(?<![\d.])(\d{3,4})(?![\d])", re.sub(r"80\s*\+|80\s*plus", " ", low))
              if int(x) % 50 == 0}
    ws = {w for w in ws if 200 <= w <= 2000}
    watt = ws.pop() if len(ws) == 1 and not showcase else None
    form = "SFX" if re.search(r"(?<![a-z])sfx", low) else "ATX" if re.search(r"(?<![a-z])atx", low) else None
    cert = m[1].title() if (m := re.search(r"(white|bronze|silver|gold|platinum|titanium)", low)) else None
    vendor = find_vendor(t, PSU_VENDORS, PSU_SERIES_VENDOR, o["brand"])
    toks = [x for x in model_tokens(t, vendor, extra_stop={"блок", "питания"}) if not PSU_TOK_JUNK.fullmatch(x)]
    return {"vendor": vendor, "watt": watt, "form": form, "cert": cert, "color": color_of(t, o["wbc"]), "tokens": toks}


def psu_len(offs) -> int | None:
    """Длина БП по габаритам WB. Оси продавцы путают, но ширина стандартна
    (ATX 150 мм, SFX 125 мм), высота меньше всех, а длина — оставшееся."""
    vals = []
    for o in offs:
        dims = []
        for n in ("Высота предмета", "Глубина предмета", "Ширина предмета"):
            if m := re.match(r"(\d+(?:[.,]\d+)?)\s*(см|мм)?", o["props"].get(n) or ""):
                x = float(m[1].replace(",", "."))
                dims.append(x if m[2] == "мм" else x * 10)
        if len(dims) != 3:
            continue
        _, a, b = sorted(dims)
        near = lambda x: any(abs(x - w) <= 3 for w in (150, 125))
        length = b if near(a) else a if near(b) else b
        if 90 <= length <= 260:
            vals.append(length)
    return round(statistics.median_low(vals)) if vals else None


def psu_modular(v: str) -> str | None:
    t = v.lower()
    return "semi" if re.search(r"полу|частич|semi", t) else "full" if re.search(r"полност|full|да", t) else \
        "no" if re.search(r"нет|немодул", t) else None


def build_psu(rows):
    groups, junk = by_model(rows, PSU_JUNK, psu_parse)
    items, unknown = [], 0
    for mid, offs in groups.items():
        watt = common(o["watt"] for o in offs) or prop_num(offs, ("Мощность устройства (Вт)", "Мощность (номинал)",
                                                                  "Мощность блока питания, Вт", "Мощность"), 200, 2000)
        if not watt:
            unknown += len(offs)
            continue
        cert = common(o["cert"] for o in offs) or common(prop_vals(
            offs, ("Сертификат 80 PLUS", "Сертификация 80 Plus", "Сертификация"),
            lambda v: (m := re.search(r"white|bronze|silver|gold|platinum|titanium", v, re.I)) and m[0].title()
            or ("Standard" if re.search(r"80\s*\+|80\s*plus|standar", v, re.I) else None)))   # у Ozon «Standart»
        items.append({"id": f"w{mid}", "name": offs[0]["md"]["name"][:80], "vendor": common(o["vendor"] for o in offs),
                      "watt": watt, "form": common(o["form"] for o in offs) or "ATX", "cert": cert,
                      "color": common(o["color"] for o in offs),
                      # Ozon (см) и DNS (мм) — прямо, WB — по габаритам
                      "len": prop_num(offs, ("Длина, см", "Длина"), 90, 260) or psu_len(offs),
                      "modular": common(prop_vals(offs, ("Модульное подключение кабелей", "Модульный",
                                                         "Модульность", "Отстегивающиеся кабели"), psu_modular)),
                      **summarize(offs)})
    return items, {"junk": junk, "unknown": unknown}


# ---------------------------------------------------------------- выгрузка


HIST = OUT.parent.parent / "app" / "history.json"
HIST_SQL = """
SELECT o.model_id, m.code, (ps.collected_at AT TIME ZONE 'Europe/Moscow')::date, min(ps.price)
FROM price_snapshots ps
JOIN offers o       ON o.id = ps.offer_id
JOIN marketplaces m ON m.id = o.marketplace_id
WHERE o.model_id IS NOT NULL AND ps.in_stock
GROUP BY 1, 2, 3 ORDER BY 3
"""


def price_history(conn, data: dict) -> dict:
    """История цен для карточки детали: по модели каталога и площадке — дневной минимум
    среди «в наличии». Хранятся только изменения цены и последний снимок: сбор идёт
    порциями, цена между снимками чаще стоит на месте, и файл растёт с числом изменений,
    а не дней. Цены дальше OUTLIER раз от цены модели — чужие товары, как в summarize()."""
    items = {int(it["id"][1:]): it for v in data.values() if isinstance(v, list) for it in v}
    days, index, raw = [], {}, defaultdict(dict)
    for mid, mp, day, price in conn.execute(HIST_SQL):
        it = items.get(MERGED.get(mid, mid))
        if not it or not it["price"] / OUTLIER <= float(price) <= it["price"] * OUTLIER:
            continue
        d = day.isoformat()
        if d not in index:
            index[d] = len(days)
            days.append(d)
        day_min = raw[(it["id"], mp)]           # у слитых моделей — минимум за день по всем
        day_min[index[d]] = min(day_min.get(index[d], 1e12), round(float(price)))
    models = defaultdict(dict)
    for (cid, mp), by_day in raw.items():
        pts = sorted(by_day.items())
        models[cid][mp] = [list(p) for i, p in enumerate(pts)
                           if i in (0, len(pts) - 1) or p[1] != pts[i - 1][1]]
    return {"days": days, "models": models}


def ali_variants(title: str, cat: str) -> bool:
    """Объявление AliExpress сразу на несколько вариантов («8gb 16gb 32gb», «3200MHz 3600MHz», DDR4 и DDR5):
    цена в выдаче — «от», и чьего варианта — не понять. Пока варианты не разбираются по карточке — не берём."""
    t = title.lower()
    caps = set(re.findall(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*(?:gb|гб|g|tb|тб)(?![a-zа-я])", t))
    freqs = set(re.findall(r"(?<!\d)(\d{4})\s*(?:mhz|мгц)", t))
    return len(caps) >= 3 or len(freqs) >= 2 or cat == "ram" and "ddr4" in t and "ddr5" in t


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    with connect() as conn:
        rows = conn.execute(SQL).fetchall()
        # Когда собраны цены — не то же, что когда пересобран каталог
        lo, hi = conn.execute("SELECT min(collected_at), max(collected_at) FROM price_snapshots").fetchone()
    by_cat = defaultdict(list)
    variants = defaultdict(int)
    for oid, cat, mp, title, brand, url, wbc, price, stock, mid, mname, mbrand, mart, mspecs, props in rows:
        if mp == "ali" and ali_variants(title, cat):
            variants[cat] += 1
            continue
        by_cat[cat].append({"id": oid, "mp": mp, "title": norm(title), "brand": brand, "url": url,
                            "wbc": wbc, "price": float(price), "in_stock": stock, "model": mid,
                            "md": {"name": mname, "brand": mbrand, "article": mart, "specs": mspecs},
                            "props": props or {}})
    data = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "prices": {"from": lo.astimezone().date().isoformat(), "to": hi.astimezone().date().isoformat()} if hi else None,
            "stats": {}}
    for cat, fn in (("gpu", build_gpu), ("case", build_case), ("cooler", build_cooler), ("mb", build_mb), ("ssd", build_ssd),
                    ("hdd", build_hdd), ("cpu", build_cpu), ("ram", build_ram), ("psu", build_psu)):
        items, st = fn(by_cat[cat])
        items.sort(key=lambda x: (-x["offers"], x["price"]))
        data[cat] = items
        used = sum(i["offers"] for i in items)
        if dropped := sum(i.get("dropped", 0) for i in items):
            st["price_outliers"] = dropped
        if variants[cat]:
            st["ali_variants"] = variants[cat]
        data["stats"][cat] = {"offers": len(by_cat[cat]), "used": used, "models": len(items), **st}
        print(f"{cat:6}: предложений {len(by_cat[cat]):4}, в каталоге {used:4} → моделей {len(items):3}  {st}")
    data["fps"] = perf.model_params()
    for cat in ("gpu", "cpu"):
        if miss := sorted({i.get("chip") or i["model"] for i in data[cat] if i["perf"] is None}):
            print(f"без балла {cat}: {', '.join(miss)}")
    OUT.write_text("// Сгенерировано collector/catalog.py — не править руками.\n"
                   "window.PCB_CATALOG = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"записано: {OUT} ({OUT.stat().st_size // 1024} КБ)")
    with connect() as conn:
        hist = price_history(conn, data)
    HIST.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    moved = sum(1 for m in hist["models"].values() if any(len(p) > 1 for p in m.values()))
    print(f"история цен: {HIST} ({HIST.stat().st_size // 1024} КБ), дней {len(hist['days'])}, "
          f"моделей {len(hist['models'])}, с изменением цены {moved}")


if __name__ == "__main__":
    main()
