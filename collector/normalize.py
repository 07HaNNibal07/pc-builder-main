"""Нормализация: сопоставление предложений площадок с моделями производителя.

Одна и та же деталь на пяти площадках называется по-разному, а нечёткое
сравнение названий одинаковые и разные товары не разделяет (на реальных парах
видеокарт 0,58 для одной карты против 0,61 для разных). Поэтому этапы идут от
надёжного к приблизительному, и каждое решение записывается со своим методом:

1. Артикул производителя (method='article'). Берётся из поля артикула площадки:
   квадратные скобки у DNS, скобки в конце названия у Ozon, WB и Маркета.
   Затем найденные артикулы ищутся во всех названиях отдельным словом. Обрезанные
   многоточием названия WB сравниваются по началу артикула. У видеокарт и SSD
   артикулы, стоящие прямо в тексте (ZT-B50600H-10M), извлекаются по форме.
2. Модельное имя эталона (method='name'). У плат, корпусов и кулеров артикула в
   названиях почти нет, зато названия DNS чистые: «ASRock B760M Pro RS/D4 WiFi».
   Имя модели из названия DNS ищется в названиях других площадок у того же
   производителя; цвет, объём и другие жёсткие признаки при этом не должны
   расходиться — чёрный и белый корпус одной серии — разные товары.
3. Признаки из названия (method='fuzzy'), для оставшихся. Производитель и
   обязательные признаки (чип, чипсет, объём) должны совпасть, сходство
   считается по словам модели и мягким признакам. Предложение присоединяется
   к модели, только если лучший кандидат однозначен. Иначе предложения с
   одинаковыми признаками образуют новую модель.

Ручные решения (method='manual') повторный запуск не трогает, идентификаторы
моделей между запусками сохраняются (ключ модели — в specs.key).

Запуск (Windows-Python):  .venv-win/Scripts/python.exe -m collector.normalize [gpu ssd mb case cooler]
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable

from collector.catalog import (CASE_JUNK, COOLER_JUNK, CPU_JUNK, GPU_JUNK, GPU_SERIES, GPU_SERIES_VENDOR,
                               GPU_VENDORS, HDD_JUNK, MB_JUNK, PSU_JUNK, RAM_JUNK, SSD_JUNK, cap_label, case_parse,
                               color_of, cooler_parse, cpu_parse, cpu_socket, display_name, find_vendor, gpu_chip,
                               gpu_mem, hdd_parse, mb_parse, mb_socket, merge_partial, name_key, norm, psu_parse,
                               ram_parse, ssd_parse)
from collector.db import connect

SQL = """
SELECT o.id, m.code, o.source_name, o.brand,
       CASE WHEN m.code = 'wb' THEN o.raw -> 'colors' END,
       p.price, p.in_stock, pr.props
FROM offers o
JOIN categories c   ON c.id = o.category_id
JOIN marketplaces m ON m.id = o.marketplace_id
LEFT JOIN offer_props pr ON pr.offer_id = o.id
LEFT JOIN LATERAL (
    SELECT price, in_stock FROM price_snapshots ps
    WHERE ps.offer_id = o.id ORDER BY collected_at DESC LIMIT 1
) p ON true
WHERE c.code = %s
  AND NOT EXISTS (SELECT 1 FROM matches mt WHERE mt.offer_id = o.id AND mt.method = 'manual')
"""

# Порог и зазор третьего этапа: ниже порога или при двух близких кандидатах
# решение не принимается — лучше новая модель, чем чужая.
FUZZY_MIN, FUZZY_GAP = 0.7, 0.1

lo = lambda v: (v or "").lower()

# ---------------------------------------------------------------- артикул


def compact(s: str) -> str:
    """Артикул без регистра, пробелов и дефисов: GV-N5060GAMING OCV2-8GD и
    GV-N5060GAMINGOCV2-8GD — одно и то же."""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


# Приставка + название чипа — код продавца, общий для разных исполнений (PA-RTX5070Ti)
CHIP_IN = {"gpu": re.compile(r"(RTX|GTX|GT|RX|ARC)\d{3,4}(TI|SUPER|XTX|XT|GRE)?"),
           "mb": re.compile(r"[ABHXZQ]\d{2,3}E?M?")}
NOT_ART = re.compile(r"GDDR|DDR\d|\d+\s*BIT|HDMI|PCI|MHZ|\bDP\b|,|;|LGA\s?\d{3,4}|SOCKET|\bAM[45]\b|\bM?-?ATX\b|\bITX\b",
                     re.I)


def plausible(a: str, cat: str) -> bool:
    """Похоже ли на артикул: латиница с цифрами, не короче 8 знаков, без
    перечня характеристик и не одно название чипа с приставкой."""
    if re.search(r"[А-Яа-яЁё]", a) or NOT_ART.search(a):
        return False
    c = compact(a)
    if not (8 <= len(c) <= 40 and re.search(r"[A-Z]", c) and re.search(r"\d", c)):
        return False
    rx = CHIP_IN.get(cat)
    return not rx or len(rx.sub("", c, count=1)) >= 5


def explicit_article(mp: str, title: str, cat: str) -> tuple[str | None, bool]:
    """Артикул из поля площадки и признак, что он обрезан многоточием.
    У DNS после скобок бывает цвет: «[PA120-BLACK] черный»."""
    m = re.search(r"\[([^\[\]]+)\]\s*(?:[а-яё]+\s*)?$", title, re.I)
    if m and plausible(m[1], cat):
        return m[1].strip(), False
    if mp in ("ozon", "wb", "ym"):
        m = re.search(r"\(([^()]+)\)\s*$", title)
        if m and plausible(m[1], cat):
            return m[1].strip(), False
        m = re.search(r"\(([^()]+)…\s*$", title)
        if m and plausible(m[1], cat):
            return m[1].strip(), True
    return None, False


# Артикул производителя в характеристиках карточки (collector.cards)
PROP_ART = ("Партномер", "Код производителя")      # WB и Ozon; DNS — в квадратных скобках


def prop_article(o: dict, cat: str) -> str | None:
    """Артикул из карточки — когда в названии его нет: «Kingston DDR4 DIMM 1x8Гб»
    у WB, «MSI MAG A850GL PCIE5 черный» у DNS. Продавцы WB пишут туда и мусор
    («Без номера», «RTX 5080») — его отсекает тот же plausible()."""
    if cat == "cpu":      # процессор — модель целиком; артикул дробит её по степпингам (… SRL4Q)
        return None
    for k in PROP_ART:
        v = (o["props"].get(k) or "").strip(" []")
        if v and plausible(v, cat):
            return v
    return None


def code_like(a: str) -> bool:
    """Партномер, а не название: цифра в каждом слове (90MB1IR0-M0EAY0,
    CM8071504555227 SRL4T). «B650M PRO RS» — название, и в чужом названии
    «B650M Pro RS WiFi» оно найдётся как часть другой платы."""
    return all(re.search(r"\d", t) for t in a.split())


# После привязки по артикулу эти признаки обязаны совпадать с большинством группы:
# продавцы ставят артикул одной планки на комплект из двух, DDR4-плату — на DDR5
ART_CHECK = {"ram": ("cap", "kit", "type"), "mb": ("mem",)}


TOKEN = re.compile(r"(?<![A-Za-z0-9-])([A-Za-z0-9]+(?:-[A-Za-z0-9]+)+)(?![A-Za-z0-9-])")


# Артикулы жёстких дисков пишутся без дефиса, зато узнаются по форме:
# ST2000DM008 (Seagate), WD20EZBX (WD), HDWD120UZSVA (Toshiba), MG08ACA16TE, HUS726T4TALA6L4
HDD_ART = re.compile(r"(?<![A-Z0-9])(ST\d{3,5}[A-Z]{2}\d{3,4}[A-Z]?|WD\d{2,4}[A-Z]{4,5}|HDW[A-Z]\d{3}[A-Z0-9]{0,6}"
                     r"|MG\d{2}[A-Z]{3}\d{2,3}[A-Z0-9]{0,3}|HU[HS]\d{3}[A-Z0-9]{5,10})(?![A-Z0-9])")


def token_articles(title: str, cat: str) -> list[str]:
    """Артикулы прямо в тексте названия: слово с дефисом, буквами и цифрами."""
    text = title.replace("…", " ")
    found = TOKEN.findall(text) + (HDD_ART.findall(text.upper()) if cat == "hdd" else [])
    return [t for t in found if plausible(t, cat)]


def compact_map(title: str) -> tuple[str, list[int]]:
    """Сжатое название и позиция каждого его символа в исходном — чтобы
    проверить, что найденная строка не часть более длинного слова."""
    chars, pos = [], []
    for i, ch in enumerate(title.upper()):
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            chars.append(ch)
            pos.append(i)
    return "".join(chars), pos


def is_word(title: str, a: int, b: int) -> bool:
    al = lambda i: 0 <= i < len(title) and title[i].isascii() and title[i].isalnum()
    return not al(a - 1) and not al(b + 1)


def occurs(title: str, cm: tuple[str, list[int]], c: str) -> bool:
    """Сжатая строка c стоит в названии отдельным словом (или словами)."""
    ct, pos = cm
    i = ct.find(c)
    while i >= 0:
        if is_word(title, pos[i], pos[i + len(c) - 1]):
            return True
        i = ct.find(c, i + 1)
    return False


def find_refs(title: str, refs: dict[str, str]) -> str | None:
    """Самый длинный известный артикул, стоящий в названии отдельным словом."""
    cm = compact_map(title)
    best = None
    for c in refs:
        if (best is None or len(c) > len(best)) and occurs(title, cm, c):
            best = c
    return best


# ---------------------------------------------------------------- категории


def gpu_attrs(o: dict) -> dict:
    t = o["title"]
    up = re.sub(r"\[.*?\]", " ", t.upper())
    found = {s: m.start() for s in GPU_SERIES if s not in ("WHITE", "ICE", "SNOW")
             and (m := re.search(r"(?<![A-Z0-9])" + s + r"(?![A-Z0-9])", up))}
    return {"vendor": find_vendor(t, GPU_VENDORS, GPU_SERIES_VENDOR, o["brand"]), "chip": gpu_chip(t),
            "mem": gpu_mem(t), "color": color_of(t, o["wbc"]),
            "tokens": sorted(found, key=found.get)}          # серии — в порядке, как в названии


def chip_name(chip) -> str | None:
    return None if not chip else f"{chip[0]} {chip[1]}" + (f" {chip[2]}" if chip[2] else "")


@dataclass
class Cat:
    junk: re.Pattern
    parse: Callable[[dict], dict]
    group_key: Callable[[dict, dict], tuple]   # ключ новой модели по признакам
    strip: str                                 # категория в начале названия — для наименования
    required: tuple = ()                       # должны быть известны и совпасть
    hard: tuple = ()                           # не должны расходиться, если известны оба
    soft: tuple = ()                           # совпадение повышает сходство
    partial: tuple = ()                        # ведущие поля ключа, которые могут быть неизвестны
    names: str | None = None                   # как вырезать имя модели из названия DNS
    token_arts: bool = False                   # артикулы по форме прямо в тексте
    empty_ok: bool = False                     # без слов модели сравнивать только по признакам
    head_brand: bool = False                   # без известного бренда первое слово — бренд, а не модель
                                               # (где в ключе нет объёма или мощности: корпуса, кулеры)


CATS = {
    "gpu": Cat(GPU_JUNK, gpu_attrs, strip=r"^(игровая\s+)?видеокарта\s+(для\s+пк\s+)?",
               group_key=lambda a, o: (a["mem"], a["color"], lo(a["vendor"]), a["chip"], tuple(a["tokens"])),
               required=("chip",), hard=("mem", "color"), soft=("mem", "color"), partial=("mem", "color"),
               token_arts=True, empty_ok=True),
    "ssd": Cat(SSD_JUNK, ssd_parse, strip=r"^(\d+\s*(гб|тб)\s+)?.*?накопитель\s+",
               group_key=lambda a, o: (a["form"], lo(a["vendor"]), a["cap"], name_key(a["tokens"], o["id"])),
               required=("cap",), hard=("form",), partial=("form",), names=r"^.*?накопитель\s+", token_arts=True),
    "mb": Cat(MB_JUNK, mb_parse, strip=r"^материнская плата\s+",
              group_key=lambda a, o: (a["mem"], lo(a["vendor"]), a["chip"], a["form"],
                                      tuple(x.upper() for x in a["tokens"][:5]) or ("#", o["id"])),
              required=("chip",), hard=("form", "mem"), soft=("mem",), partial=("mem",),
              names=r"^материнская плата\s+"),
    "case": Cat(CASE_JUNK, case_parse, strip=r"^(компьютерный\s+|игровой\s+)?корпус\s+(для\s+пк\s+)?",
                group_key=lambda a, o: (a["color"], lo(a["vendor"]), name_key(a["tokens"], o["id"])),
                hard=("color",), partial=("color",), names=r"^корпус\s+", head_brand=True),
    "cooler": Cat(COOLER_JUNK, cooler_parse,
                  strip=r"^(башенный\s+)?(кулер|вентилятор|устройство охлаждения\s*\(кулер\))\s+(башенный\s+)?"
                        r"(для\s+процессора\s+|процессора\s+|процессорный\s+)?",
                  group_key=lambda a, o: (a["color"], lo(a["vendor"]), name_key(a["tokens"], o["id"])),
                  hard=("color",), partial=("color",), names=r"^кулер для процессора\s+", head_brand=True),
    "hdd": Cat(HDD_JUNK, hdd_parse, strip=r"^.*?(жесткий|жёсткий)\s+диск\s+(hdd\s+)?(3[.,]5\S*\s+)?",
               group_key=lambda a, o: (lo(a["vendor"]), a["cap"], name_key(a["tokens"], o["id"])),
               required=("cap",), hard=("rpm",), soft=("rpm",), names=r"^жесткий диск\s+", token_arts=True),
    # Процессор определяется моделью целиком: слов модели сверх неё нет, OEM и BOX — разные товары
    "cpu": Cat(CPU_JUNK, cpu_parse, strip=r"^(центральный\s+)?процессор\s+",
               group_key=lambda a, o: (a["pack"], a["cpu"]),
               required=("cpu",), hard=("pack",), partial=("pack",), empty_ok=True),
    "ram": Cat(RAM_JUNK, ram_parse, strip=r"^(оперативная\s+память|модуль\s+памяти|память)\s+",
               group_key=lambda a, o: (a["type"], a["kit"], a["freq"], a["color"], lo(a["vendor"]), a["cap"],
                                       name_key(a["tokens"], o["id"])),
               required=("cap",), hard=("type", "kit", "freq", "color"), soft=("freq",),
               partial=("type", "kit", "freq", "color"), names=r"^оперативная память\s+", token_arts=True),
    "psu": Cat(PSU_JUNK, psu_parse, strip=r"^(игровой\s+)?блок\s+питания\s+(для\s+пк\s+|компьютера\s+|пк\s+)?",
               group_key=lambda a, o: (a["form"], a["color"], lo(a["vendor"]), a["watt"], name_key(a["tokens"], o["id"])),
               required=("watt",), hard=("form", "color"), soft=("cert",), partial=("form", "color"),
               names=r"^блок питания\s+", token_arts=True),
}


def branded(a: dict, spec: Cat) -> dict:
    """Признаки для ключа новой модели. Бренда нет в справочнике и на площадке
    (у Ali почти всегда) — первым словом идёт он сам, и ключ из двух первых слов
    склеивал всё подряд: «Power Train Flying» и «Power Train ES500» — одна модель.
    Первое слово считаем брендом; без слов модели после него товар остаётся отдельным."""
    if a["vendor"] or not spec.head_brand or not a["tokens"]:
        return a
    return {**a, "vendor": a["tokens"][0], "tokens": a["tokens"][1:]}

COLOR_TAIL = re.compile(r"\s+(черный|черная|белый|белая|серый|серая|серебристый|розовый|синий|красный|зеленый|"
                        r"бежевый|коричневый|прозрачный|разноцветный)\s*$", re.I)


def dns_model(o: dict, spec: Cat) -> str | None:
    """Имя модели из названия DNS: без категории, артикула, цвета и производителя."""
    t = re.sub(spec.names, "", o["title"], flags=re.I)
    t = COLOR_TAIL.sub("", re.split(r"\s*\[", t)[0])
    v = o["attrs"]["vendor"]
    if v and t.lower().startswith(v.lower()):
        t = t[len(v):]
    c = compact(t)
    ok = len(c) >= 5 or (len(c) >= 2 and re.search(r"[A-Z]", c) and re.search(r"\d", c))
    return t.strip(" -") if ok else None


def compatible(a: dict, b: dict, spec: Cat) -> bool:
    return all(a[f] is None or b[f] is None or a[f] == b[f] for f in spec.required + spec.hard)


def common(values):
    vals = [v for v in values if v is not None]
    return Counter(vals).most_common(1)[0][0] if vals else None


def model_attrs(offers: list[dict]) -> dict:
    """Признаки модели по всем её предложениям. Слова модели — из самого
    подробного названия: у Ozon в названии часто только артикул."""
    a = [o["attrs"] for o in offers]
    res = {k: common(x[k] for x in a) for k in a[0] if k != "tokens"}
    res["tokens"] = max((x["tokens"] for x in a), key=len)
    return res


def similarity(o: dict, m: dict, spec: Cat) -> float | None:
    """Сходство предложения с моделью. None — несовместимы или сравнивать не по чему."""
    if not o["vendor"] or lo(o["vendor"]) != lo(m["vendor"]):
        return None
    if any(o[f] is None or o[f] != m[f] for f in spec.required) or not compatible(o, m, spec):
        return None
    parts = lambda ts: {p for t in ts for p in t.upper().split("-") if p}    # GM-800B = GM 800B
    so, sm = parts(o["tokens"]), parts(m["tokens"])
    if not so and not spec.empty_ok:
        return None
    jac = len(so & sm) / len(so | sm) if so | sm else 1.0
    eq = lambda f: 1.0 if o[f] is not None and o[f] == m[f] else 0.5
    return round((1 - 0.2 * len(spec.soft)) * jac + sum(0.2 * eq(f) for f in spec.soft), 4)


def canonical_name(cat: str, spec: Cat, offers: list[dict], a: dict) -> str:
    """Наименование модели: у DNS названия чистые — берём его; без DNS
    собираем из признаков, потому что названия WB и Ozon полны мусора."""
    dns = [o for o in offers if o["mp"] == "dns"]
    if cat == "cpu" and a["cpu"]:
        return f"{a['vendor']} {a['cpu']}" + (" BOX" if a["pack"] == "BOX" else "")
    if cat == "gpu" and a["chip"] and not dns:
        parts = [a["vendor"], chip_name(a["chip"]), " ".join(s.title() if len(s) > 3 else s for s in a["tokens"]),
                 f"{a['mem']} ГБ" if a["mem"] else None, "белая" if a["color"] == "white" else None]
        return " ".join(p for p in parts if p)[:512]
    if dns or not a["tokens"]:
        name = display_name(dns or offers, spec.strip)
        if a["vendor"] and a["vendor"].lower() not in name.lower():
            name = f"{a['vendor']} {name}"
        # У DNS память в названии не пишут — без неё Dual и Dual OC на 8 ГБ и 16 ГБ неотличимы
        if cat == "gpu" and a["mem"] and not re.search(rf"\b{a['mem']}\s*(ГБ|GB)", name, re.I):
            name += f" {a['mem']} ГБ"
    else:
        name = " ".join(filter(None, [a["vendor"], " ".join(a["tokens"][:4])]))
    if cat in ("ssd", "hdd") and a.get("cap") and cap_label(a["cap"]) not in name:
        name += " " + cap_label(a["cap"])
    if cat == "ram":
        # объём бывает не распознан при известном числе модулей (новые предложения DNS 13.09)
        per = a["cap"] // a["kit"] if a["cap"] and a["kit"] and a["kit"] > 1 else None
        name = " ".join(filter(None, [name, f"{a['cap']} ГБ" if f"{a['cap']} ГБ" not in name else None,
                                      f"({a['kit']}×{per} ГБ)" if per else None,
                                      "-".join(str(x) for x in (a["type"], a["freq"]) if x) or None]))
    if cat == "psu" and a["watt"] and str(a["watt"]) not in name:
        name += f" {a['watt']} Вт"
    return name[:512]


def specs_of(cat: str, a: dict) -> dict:
    if cat == "gpu":
        return {"chip": chip_name(a["chip"]), "mem": a["mem"], "color": a["color"], "series": a["tokens"]}
    if cat == "ssd":
        return {"cap": a["cap"], "form": a["form"], "iface": a["iface"]}
    if cat == "hdd":
        return {"cap": a["cap"], "form": "3.5", "rpm": a["rpm"]}
    if cat == "mb":
        return {"chipset": a["chip"], "form": a["form"], "mem": a["mem"],
                "socket": mb_socket(a["chip"]) if a["chip"] else None}
    if cat == "cpu":
        # Модель по артикулу бывает и без распознанного названия процессора
        return {"cpu": a["cpu"], "socket": cpu_socket(a["cpu"]) if a["cpu"] else None, "pack": a["pack"]}
    if cat == "ram":
        return {"type": a["type"], "cap": a["cap"], "kit": a["kit"], "freq": a["freq"], "color": a["color"]}
    if cat == "psu":
        return {"watt": a["watt"], "form": a["form"], "cert": a["cert"], "color": a["color"]}
    return {"color": a["color"]}


# ---------------------------------------------------------------- этапы


def normalize(cat: str, rows: list[dict]):
    spec = CATS[cat]
    offers, junk = [], 0
    for o in rows:
        if spec.junk.search(o["title"]):
            junk += 1
            continue
        o["attrs"] = spec.parse(o)
        offers.append(o)

    models: dict[str, dict] = {}                 # ключ модели → артикул и предложения

    def attach(o, key, method, sim, article=None):
        models.setdefault(key, {"article": article, "offers": []})["offers"].append(o)
        o["match"], o["mkey"] = (method, sim), key

    # 1а. Артикулы из полей площадок — справочник (записи DNS самые чистые)
    refs: dict[str, str] = {}
    for o in sorted(offers, key=lambda o: o["mp"] != "dns"):
        o["art"], o["cut"] = explicit_article(o["mp"], o["raw_title"], cat)
        if o["art"] and not o["cut"]:
            refs.setdefault(compact(o["art"]), o["art"])
        # Артикул из карточки — запасной: само предложение привязывается по нему лишь на
        # этапе 2б, а в чужих названиях ищется, только если это партномер, а не название
        elif not o["art"] and (pa := prop_article(o, cat)):
            o["prop_art"] = pa
            if code_like(pa):
                refs.setdefault(compact(pa), pa)
    # 1б. Справочные артикулы в названиях; обрезанные — по началу; 1в — по форме в тексте
    for o in offers:
        hit, sim = find_refs(o["raw_title"], refs), 1.0
        if not hit and o["art"] and o["cut"]:
            pre = compact(o["art"])
            cands = [c for c in refs if c.startswith(pre)]
            if len(cands) == 1 and len(pre) >= 10:
                hit, sim = cands[0], 0.9
        if not hit and spec.token_arts:
            toks = token_articles(o["raw_title"], cat)
            if toks:
                t = max(toks, key=len)
                hit = compact(t)
                refs.setdefault(hit, t)
        if hit:
            attach(o, "A:" + hit, "article", sim, refs[hit])

    # 2. Модельные имена эталона: имя из названия DNS — в названиях других площадок
    by_name = 0
    if spec.names:
        name_refs = []
        for o in offers:
            if o["mp"] != "dns" or not (model := dns_model(o, spec)):
                continue
            key = o.get("mkey") or "N:" + json.dumps(
                [lo(o["attrs"]["vendor"]), compact(model), [o["attrs"][f] for f in spec.required + spec.hard]],
                ensure_ascii=False)
            name_refs.append({"vendor": lo(o["attrs"]["vendor"]), "c": compact(model), "attrs": o["attrs"], "key": key})
        for o in offers:
            if "match" in o:
                continue
            a, cm = o["attrs"], compact_map(o["raw_title"])
            hits = [r for r in name_refs
                    if (lo(a["vendor"]) == r["vendor"] if a["vendor"] else len(r["c"]) >= 5)
                    and compatible(a, r["attrs"], spec) and occurs(o["raw_title"], cm, r["c"])]
            if hits:
                best = max(len(h["c"]) for h in hits)
                keys = {h["key"] for h in hits if len(h["c"]) == best}
                if len(keys) == 1:                       # два эталона одной длины — неоднозначно
                    attach(o, keys.pop(), "name", 1.0)
                    by_name += 1

    # 2б. Артикул из карточки — оставшимся после эталона DNS: иначе партномер WB
    # «90MB14I0-M0EAY0» увёл бы плату из группы «ASUS PRIME B550M-A» DNS и ЯМ
    for o in offers:
        if "match" not in o and o.get("prop_art"):
            c = compact(o["prop_art"])
            attach(o, "A:" + c, "article", 0.9, models.get("A:" + c, {}).get("article") or o["prop_art"])

    # Проверка групп по артикулу: противоречащие большинству предложения — на этап 3.
    # Только там, где продавцы путают исполнения; объём SSD/HDD из названия разбирается
    # хуже, чем артикул, — по нему группу не рвём
    check = ART_CHECK.get(cat, ())
    for m in models.values():
        a = model_attrs(m["offers"])
        bad = [o for o in m["offers"] if any(o["attrs"][f] is not None and a[f] is not None
                                             and o["attrs"][f] != a[f] for f in check)]
        if bad and len(bad) < len(m["offers"]):
            m["offers"] = [o for o in m["offers"] if o not in bad]
            for o in bad:
                del o["match"], o["mkey"]
    models = {k: m for k, m in models.items() if m["offers"]}

    # 3. Признаки: к однозначной модели, иначе новая модель по ключу признаков
    cands = [{"key": k, "attrs": model_attrs(m["offers"])} for k, m in models.items()]
    groups, fuzzy, unknown = defaultdict(list), 0, 0
    for o in offers:
        if "match" in o:
            continue
        a = o["attrs"]
        if any(a[f] is None for f in spec.required):
            unknown += 1
            continue
        scored = sorted(((s, c) for c in cands if (s := similarity(a, c["attrs"], spec)) is not None),
                        key=lambda x: -x[0])
        if scored and scored[0][0] >= FUZZY_MIN and (len(scored) == 1 or scored[0][0] - scored[1][0] >= FUZZY_GAP):
            attach(o, scored[0][1]["key"], "fuzzy", scored[0][0])
            fuzzy += 1
            continue
        groups[spec.group_key(branded(a, spec), o)].append(o)
    if spec.partial:
        merge_partial(groups, spec.partial)
    for key, offs in groups.items():
        k = "G:" + json.dumps(key, ensure_ascii=False)
        for o in offs:
            attach(o, k, "fuzzy", None)

    out = []
    for key, m in models.items():
        a = model_attrs(m["offers"])
        out.append({"key": key, "article": m["article"], "offers": m["offers"], "brand": a["vendor"] or "н/д",
                    "name": canonical_name(cat, spec, m["offers"], a), "specs": {**specs_of(cat, a), "key": key}})
    st = {"offers": len(rows), "junk": junk, "unknown": unknown,
          "article": sum(1 for o in offers if o.get("match", ("",))[0] == "article"),
          "name": by_name, "fuzzy_joined": fuzzy, "new_models": len(groups)}
    return out, st


# ---------------------------------------------------------------- запись


def save(conn, cat: str, models: list[dict]):
    cat_id = conn.execute("SELECT id FROM categories WHERE code = %s", (cat,)).fetchone()[0]
    existing = conn.execute("SELECT id, article, specs->>'key' FROM models WHERE category_id = %s",
                            (cat_id,)).fetchall()
    by_key = {k: i for i, _, k in existing if k}
    by_compact = {compact(a): i for i, a, _ in existing if a}
    prev = dict(conn.execute("SELECT id, model_id FROM offers WHERE category_id = %s AND model_id IS NOT NULL",
                             (cat_id,)).fetchall())

    conn.execute("""DELETE FROM matches WHERE method <> 'manual' AND offer_id IN
                    (SELECT id FROM offers WHERE category_id = %s)""", (cat_id,))
    conn.execute("""UPDATE offers o SET model_id = NULL WHERE category_id = %s AND NOT EXISTS
                    (SELECT 1 FROM matches mt WHERE mt.offer_id = o.id AND mt.method = 'manual')""", (cat_id,))
    used = set()
    for m in models:
        mid = by_key.get(m["key"]) or (by_compact.get(compact(m["article"])) if m["article"] else None)
        m["id"] = mid if mid not in used else None     # старый id уже занят другой моделью этого запуска
        used.add(m["id"])
    # Ключ новый (поменялись правила разбора) — id той старой модели, откуда пришло
    # большинство предложений: id живёт в сохранённых сборках и ссылках
    for m in models:
        if m["id"] is None:
            mid, n = Counter(prev.get(o["id"]) for o in m["offers"]).most_common(1)[0]
            if mid and mid not in used and 2 * n > len(m["offers"]):
                m["id"] = mid
                used.add(mid)
    for m in models:
        mid = m["id"]
        specs = json.dumps(m["specs"], ensure_ascii=False)
        if mid:
            conn.execute("UPDATE models SET brand = %s, article = %s, name = %s, specs = %s WHERE id = %s",
                         (m["brand"], m["article"], m["name"], specs, mid))
        else:
            mid = conn.execute("""INSERT INTO models (category_id, brand, article, name, specs)
                                  VALUES (%s, %s, %s, %s, %s) RETURNING id""",
                               (cat_id, m["brand"], m["article"], m["name"], specs)).fetchone()[0]
        used.add(mid)
        m["id"] = mid
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO matches (offer_id, model_id, similarity, method) VALUES (%s, %s, %s, %s)",
                            [(o["id"], mid, o["match"][1], o["match"][0]) for o in m["offers"]])
            cur.executemany("UPDATE offers SET model_id = %s WHERE id = %s", [(mid, o["id"]) for o in m["offers"]])
    # Модели, у которых не осталось предложений, удаляются
    conn.execute("""DELETE FROM models md WHERE category_id = %s
                    AND NOT EXISTS (SELECT 1 FROM offers o WHERE o.model_id = md.id)
                    AND NOT EXISTS (SELECT 1 FROM matches mt WHERE mt.model_id = md.id)""", (cat_id,))


def report(cat: str, models: list[dict], st: dict):
    multi = [m for m in models if len({o["mp"] for o in m["offers"]}) >= 2]
    print(f"== {cat}: предложений {st['offers']} — мусор {st['junk']}, не распознано {st['unknown']}; "
          f"по артикулу {st['article']}, по имени эталона {st['name']}, по признакам {st['fuzzy_joined']}")
    print(f"   моделей {len(models)} (с артикулом {sum(1 for m in models if m['article'])}, "
          f"новых по признакам {st['new_models']}); на двух и более площадках {len(multi)}")
    # Самый большой разброс цены одной модели между площадками — довод для защиты
    spread = []
    for m in multi:
        best = {}
        for o in m["offers"]:
            if o["price"] and o["in_stock"]:
                best[o["mp"]] = min(best.get(o["mp"], o["price"]), o["price"])
        if len(best) >= 2:
            lo_, hi = min(best.values()), max(best.values())
            spread.append(((hi - lo_) / lo_, m["name"], best))
    for r, name, best in sorted(spread, reverse=True, key=lambda x: x[0])[:3]:
        shops = ", ".join(f"{k} {int(v):,}".replace(",", " ") for k, v in sorted(best.items(), key=lambda x: x[1]))
        print(f"     разброс {r:4.0%}  {name}  |  {shops}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cats = sys.argv[1:] or list(CATS)
    with connect() as conn:
        for cat in cats:
            rows = [{"id": i, "mp": mp, "raw_title": t, "title": norm(t), "brand": b, "wbc": wbc,
                     "price": float(p) if p is not None else None, "in_stock": bool(s), "props": props or {}}
                    for i, mp, t, b, wbc, p, s, props in conn.execute(SQL, (cat,)).fetchall()]
            models, st = normalize(cat, rows)
            save(conn, cat, models)
            report(cat, models, st)


if __name__ == "__main__":
    main()
