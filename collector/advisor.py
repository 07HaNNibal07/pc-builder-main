"""Подбор сборки — ядро СППР.

Вход: бюджет, задача (игры в выбранном разрешении, работа, офис) и предпочтения
(производитель процессора и видеокарты, наибольший форм-фактор платы).

Метод:
  1. Процессоры и видеокарты отбираются по Парето: остаются те, для которых нет детали
     не дороже и не слабее. Это сжимает перебор с сотен моделей до десятков.
  2. Для каждой пары «процессор × видеокарта × тип памяти» остальные детали — самые дешёвые
     из совместимых и достаточных: плата под сокет и память, двухканальный комплект, SSD,
     кулер под теплопакет, корпус под плату/видеокарту/кулер, блок питания с запасом.
  3. Полезность сборки: игры — среднее геометрическое FPS по играм (collector/perf.py),
     работа — многопоток процессора и сила видеокарты, офис — достаточная скорость процессора.
  4. Варианты: «оптимальная» — наибольшая полезность в бюджете; «экономная» — самая дешёвая
     с полезностью ≥ 90 % оптимальной; «с запасом» — лучшая до +20 % бюджета, если прирост ощутим.
Совместимость — жёсткие ограничения, те же, что в 3D-прототипе (check).
Планка качества: «самое дешёвое» не значит «любое» — плата, кулер и корпус только известных брендов,
блок питания — из проверенных, под мощный процессор плата не на начальном чипсете,
память не медленнее базовой для платформы. Если таких нет — берётся доступное.

Запуск: python -m collector.advisor 120000 --task game --res 1440p
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

from collector import perf

CATALOG = Path(__file__).resolve().parent.parent / "web" / "prototype-3d" / "catalog.js"

TASKS = {"game": "Игры", "work": "Работа (рендер, монтаж, сборка кода)", "office": "Офис и учёба"}
MIN_OFFERS = 2      # модель у одного продавца — чаще ошибка сопоставления или случайная цена
ECON_SHARE = 0.9    # «экономная» — не меньше этой доли полезности оптимальной
PLUS_BUDGET = 1.2   # «с запасом» — до стольких бюджетов
PLUS_GAIN = 1.08    # …если полезность выше хотя бы на 8 %

# Шаблоны корпусов — как в 3D-прототипе: чего нет в карточке, берётся отсюда
CASE_TPL = {"tower": {"maxGpu": 360, "maxCooler": 165, "slots": 7, "style": "tower"},
            "aquarium": {"maxGpu": 410, "maxCooler": 167, "slots": 7, "style": "aquarium"},
            "matx": {"maxGpu": 300, "maxCooler": 155, "slots": 4, "style": "tower"},
            "itx": {"maxGpu": 290, "maxCooler": 145, "slots": 3, "style": "sff"}}
MB_SLOTS = {"ATX": 4, "mATX": 2, "ITX": 2}          # слотов памяти, если в карточке не сказано
FORM_ORDER = ["ITX", "mATX", "ATX"]
CERTIFIED = re.compile(r"bronze|silver|gold|platinum|titanium", re.I)
HOT_CPU = re.compile(r"i[79]-\d+K|Ultra [79] \d+K|Ryzen 9")
TRUSTED_PSU = {"DEEPCOOL", "1STPLAYER", "MSI", "Zalman", "Cougar", "XPG", "ADATA", "Super Flower", "Chieftec",
               "Montech", "PCCooler", "Lian Li", "Thermalright", "THERMALRIGHT", "Gamerstorm", "Raijintek",
               "be quiet!", "Corsair", "Seasonic", "FSP", "Cooler Master", "Thermaltake", "ASUS", "GIGABYTE"}
ENTRY_CHIPSETS = {"H410", "H510", "H610", "A320", "A520", "A620"}   # слабое питание процессора
RAM_FREQ = {"DDR4": 3200, "DDR5": 5600}                             # базовая частота платформы
# Платы A520/B550 не запускают Ryzen 1000/2000 и APU 2200G–3400G, Athlon 200GE/3000G (AMD: «Zen 2 и новее, кроме
# Picasso/Raven Ridge»); на сокет эти процессоры подходят — ловушка для покупателя б/у
NEW_AM4 = {"A520", "B550"}
OLD_AM4 = re.compile(r"Ryzen \d [12]\d{3}|Ryzen \d 3[24]00G|Athlon")
RAM_SHARE = 0.25     # дороже этой доли бюджета — память вдвое меньшего объёма
DRIVE_CATS = ("ssd", "hdd")
BAY_NAMES = {"m2": "Слоты M.2", "s25": "Места под 2,5″", "h35": "Отсеки 3,5″"}


def bay_of(d: dict) -> str:
    return "m2" if (d.get("form") or "").startswith("M.2") else "h35" if d.get("form") == "3.5" else "s25"


def drive_bays(c: dict, m: dict) -> dict:
    """Мест под накопители — как в 3D-модели (baysOf): слоты M.2 из карточки платы, без неё — по
    форм-фактору; места 2,5″ и 3,5″ — по типу корпуса (в карточках корпусов их нет)."""
    m2 = m["m2"] if m.get("m2") is not None else {"ITX": 1, "mATX": 2}.get(m.get("form"), 3)
    sff = c["style"] == "sff"
    return {"m2": m2, "s25": 1 if sff else 2, "h35": 0 if sff else 2}


def drive_room(c: dict | None, m: dict | None, drives: list) -> bool:
    """Влезет ли ещё один накопитель: свободный слот M.2 или место 2,5″/3,5″ при свободном порте SATA."""
    if not c or not m:
        return False
    bays, n = drive_bays(c, m), {"m2": 0, "s25": 0, "h35": 0}
    for d in drives:
        n[bay_of(d)] += 1
    sata_free = m.get("sata") is None or n["s25"] + n["h35"] < m["sata"]
    return n["m2"] < bays["m2"] or sata_free and (n["s25"] < bays["s25"] or n["h35"] < bays["h35"])


def load(path=CATALOG) -> dict:
    s = Path(path).read_text(encoding="utf-8")
    return json.loads(s[s.index("{"): s.rindex("}") + 1])


# ---------------------------------------------------------------- совместимость

def case_view(c: dict) -> dict:
    t = CASE_TPL.get(c.get("tpl"), CASE_TPL["tower"])
    return {**c, "maxGpu": c.get("maxGpu") or t["maxGpu"], "maxCooler": c.get("maxCooler") or t["maxCooler"],
            "slots": t["slots"], "style": t["style"]}


def psu_need(g: dict | None, p: dict) -> tuple[int, bool]:
    """Мощность БП: рекомендация производителя видеокарты или оценка
    «карта + теплопакет процессора + 100 Вт, запас 20 %» — берётся большее. (Вт, это оценка?)"""
    est = math.ceil((((g.get("power") or 200) if g else 0) + (p.get("tdp") or 125) + 100) * 1.2 / 50) * 50  # как в 3D
    return (g["psu"], False) if g and g.get("psu") and g["psu"] >= est else (est, True)


def mem_slots(m: dict) -> int:
    return m.get("memSlots") or MB_SLOTS.get(m.get("form"), 2)


def cpu_tdp(p: dict) -> int:
    """Теплопакет из карточки, иначе по модели. У K-серии Intel — пиковый (PL2): в карточке пишут базовый 125 Вт,
    а под нагрузкой такой процессор берёт 181–253 Вт — ему нужны кулер и питание платы под пиковый."""
    m = p["model"]
    if re.search(r"i5-1[2-4]\d{3}K|Ultra 5 \d+K", m):
        return max(p.get("tdp") or 0, 181)
    if re.search(r"i[79]-1[2-4]\d{3}K|Ultra [79] \d+K", m):
        return max(p.get("tdp") or 0, 253)
    if p.get("tdp"):
        return p["tdp"]
    if re.search(r"X3D$", m):
        return 120
    if re.search(r"Ryzen \d \d{4}X$", m):
        return 105 if " 5 " in m or " 7 " in m else 170
    return 65


def cooler_class(p: dict) -> int:
    """0 — подойдёт любой, 1 — башенный, 2 — двухбашенный."""
    tdp = cpu_tdp(p)
    return 2 if tdp > 150 or HOT_CPU.search(p["model"]) else 1 if tdp > 65 else 0


def old_am4(p: dict) -> bool:
    return p.get("socket") == "AM4" and bool(OLD_AM4.search(p.get("model") or ""))


def heavy_cpu(p: dict) -> bool:
    """Мощному процессору нужна плата с хорошим питанием."""
    return cooler_class(p) == 2 or cpu_tdp(p) > 125


def cooler_ok(k: dict, cls: int) -> bool:
    return cls == 0 or (k.get("type") == "tower" and (cls == 1 or (k.get("towers") or 1) >= 2))


def checks(b: dict) -> list[dict]:
    """Все проверки совместимости: {ok, part, title, detail}; title — уже с итогом («Сокет — совместим»)."""
    p, m, r, g, w, k = b["cpu"], b["mb"], b["ram"], b.get("gpu"), b["psu"], b["cooler"]
    c, qty, out = case_view(b["case"]), b.get("ram_qty", 1), []

    def add(part, ok, yes, no, detail):
        out.append({"part": part, "ok": bool(ok), "title": yes if ok else no, "detail": detail})

    add("cpu", p.get("socket") == m.get("socket"), "Сокет — совместим", "Сокет — не совпадает",
        f"процессор {p.get('socket')}, плата {m.get('socket')}")
    if old_am4(p) and m.get("socket") == "AM4" and m.get("chipset"):
        add("cpu", m["chipset"] not in NEW_AM4, "Чипсет — поддерживает процессор", "Чипсет — не поддерживает процессор",
            f"{p.get('model')} на {m['chipset']}: A520 и B550 работают с Ryzen 3000 (кроме G) и новее")
    add("ram", r.get("type") == m.get("mem"), "Тип памяти — совместим", "Тип памяти — не подходит",
        f"память {r.get('type')}, плата под {m.get('mem')}")
    mods, slots = (r.get("kit") or 1) * qty, mem_slots(m)
    add("ram", mods <= slots, "Слотов памяти — хватает", "Слотов памяти — мало", f"модулей {mods}, слотов {slots}")
    if m.get("memMax") and r.get("cap"):
        add("ram", r["cap"] * qty <= m["memMax"], "Объём памяти — в пределах платы",
            "Объём памяти — больше предела платы", f"{r['cap'] * qty} ГБ, плата держит до {m['memMax']} ГБ")
    add("case", m.get("form") in c["forms"], "Плата входит в корпус", "Плата не входит в корпус",
        f"плата {m.get('form')}, корпус: {', '.join(c['forms'])}")
    if g:
        if g.get("len"):
            add("gpu", g["len"] <= c["maxGpu"], "Длина видеокарты — подходит", "Видеокарта не влезает по длине",
                f"{g['len']} мм, место до {c['maxGpu']} мм")
        if g.get("slots"):
            add("gpu", g["slots"] <= c["slots"], "Толщина видеокарты — подходит", "Видеокарта слишком толстая",
                f"{g['slots']} слота, в корпусе {c['slots']}")
    else:
        add("gpu", p.get("igpu"), "Встроенная графика — есть", "Нет ни видеокарты, ни встроенной графики",
            "изображение выводит процессор")
    add("cooler", k["h"] <= c["maxCooler"], "Высота кулера — подходит", "Кулер не влезает по высоте",
        f"{k['h']} мм, место до {c['maxCooler']} мм")
    cls = cooler_class(p)
    add("cooler", cooler_ok(k, cls), "Охлаждение — достаточно", "Кулер слабоват для процессора",
        f"теплопакет {'' if p.get('tdp') else '≈ '}{cpu_tdp(p)} Вт, нужен {('любой', 'башенный', 'двухбашенный')[cls]}")
    need, est = psu_need(g, p)
    add("psu", w["watt"] >= need, "Мощность БП — достаточно", "Мощности БП не хватает",
        f"нужно {'≈ ' if est else ''}{need} Вт, у блока {w['watt']} Вт")
    if c["style"] == "sff":
        add("psu", w.get("form") == "SFX", "Формат БП — SFX", "Компактному корпусу нужен блок SFX",
            f"блок {w.get('form')}")
    if w.get("len") and c.get("maxPsu"):
        add("psu", w["len"] <= c["maxPsu"], "Длина БП — подходит", "Блок питания не влезает по длине",
            f"{w['len']} мм, место до {c['maxPsu']} мм")
    # Накопители: основной SSD и дополнительные (b["drives"]) — места на плате и в корпусе, порты SATA
    drives = [d for d in (b.get("ssd"), *b.get("drives", ())) if d]
    if drives:
        bays, n = drive_bays(c, m), {"m2": 0, "s25": 0, "h35": 0}
        for d in drives:
            n[bay_of(d)] += 1
        for bay, k in n.items():
            if k:
                add("ssd", k <= bays[bay], f"{BAY_NAMES[bay]} — хватает", f"{BAY_NAMES[bay]} — не хватает",
                    f"накопителей {k}, мест {'на плате' if bay == 'm2' else 'в корпусе'} {bays[bay]}")
        sata = n["s25"] + n["h35"]
        if sata and m.get("sata") is not None:
            add("ssd", sata <= m["sata"], "Портов SATA — хватает", "Портов SATA — мало",
                f"накопителей SATA {sata}, портов на плате {m['sata']}")
    return out


def check(b: dict) -> list[str]:
    """Проблемы совместимости сборки; пустой список — сборка совместима."""
    return [f"{c['title']}: {c['detail']}" for c in checks(b) if not c["ok"]]


# ---------------------------------------------------------------- отбор

def pareto(items: list[dict], keys: tuple[str, ...]) -> list[dict]:
    """Недоминируемые по цене и keys: нет детали не дороже и не хуже по всем ключам."""
    out = []
    for x in sorted(items, key=lambda x: (x["price"], [-x[k] for k in keys])):
        if not any(all(y[k] >= x[k] for k in keys) for y in out):
            out.append(x)
    return out


def chip_vendor(chip: str) -> str:
    return "NVIDIA" if re.match(r"(RTX|GTX|GT)\b", chip) else "Intel" if chip.startswith("Arc") else "AMD"


def targets(task: str, budget: int) -> dict:
    """Объём памяти (ГБ) и SSD (ГБ) по задаче и бюджету."""
    if task == "office":
        return {"ram": 8 if budget < 30000 else 16, "ssd": 256 if budget < 30000 else 500}
    if task == "work":
        return {"ram": 16 if budget < 50000 else 32 if budget < 150000 else 64,
                "ssd": 500 if budget < 50000 else 1000 if budget < 150000 else 2000}
    return {"ram": 16 if budget < 70000 else 32, "ssd": 500 if budget < 60000 else 1000 if budget < 200000 else 2000}


def cheapest(items, pred=lambda x: True):
    return min((x for x in items if pred(x)), key=lambda x: x["price"], default=None)


def utility(task: str, res: int, g: dict | None, p: dict, mem: str = "DDR5") -> float:
    if task == "game":
        k = perf.mem_k(p.get("socket"), mem)
        f = [perf.fps(gm, res, g["perf"], g.get("mem"), p["perf"], memk=k)["fps"] for gm in perf.GAMES]
        return math.exp(sum(math.log(max(x, 1)) for x in f) / len(f))
    if task == "work":
        return p["mt"] ** 0.7 * (g["perf"] if g else 10) ** 0.3
    return min(p["perf"], 90) + min(p["mt"], 75) / 100         # офис: хватает ~ i3-12100 (90 / 77) — дороже не нужно


class Advisor:
    def __init__(self, cat: dict, budget: int, task: str = "game", res: int = 1,
                 cpu_vendor: str | None = None, gpu_vendor: str | None = None, max_form: str | None = None):
        self.budget, self.task, self.res = budget, task, res
        self.need = targets(task, budget)
        ok = lambda x: x.get("price") and x.get("offers", 0) >= MIN_OFFERS
        forms = FORM_ORDER[:FORM_ORDER.index(max_form) + 1] if max_form else FORM_ORDER
        self.mb = [x for x in cat["mb"] if ok(x) and x.get("socket") and x.get("mem") and x.get("form") in forms
                   and mem_slots(x) >= 2 and x.get("vendor")]
        self.ram = [x for x in cat["ram"] if ok(x) and x.get("type") and x.get("kit") == 2 and x.get("cap")]
        self.case = [case_view(x) for x in cat["case"] if ok(x) and x.get("forms") and x.get("vendor")]
        self.cooler = [x for x in cat["cooler"] if ok(x) and x.get("h") and x.get("vendor")]
        self.psu = [x for x in cat["psu"] if ok(x) and x.get("watt")]
        self.ssd = [x for x in cat["ssd"] if ok(x) and x.get("cap")]
        sockets = {m["socket"] for m in self.mb}
        cpus = [x for x in cat["cpu"] if ok(x) and x.get("perf") and x.get("socket") in sockets
                and (not cpu_vendor or x.get("vendor") == cpu_vendor) and (task != "office" or x.get("igpu"))]
        key = ("mt",) if task == "work" else ("perf",)
        self.cpus = [c for s in sockets for c in pareto([x for x in cpus if x["socket"] == s], key)]
        gpus = [{**x, "mem": x.get("mem") or 0} for x in cat["gpu"] if ok(x) and x.get("perf")
                and (not gpu_vendor or chip_vendor(x["chip"]) == gpu_vendor)]
        self.gpus = [None] if task == "office" else pareto(gpus, ("perf", "mem"))
        if task == "work":
            self.gpus.append(None)          # работа без видеокарты — если у процессора есть графика
        self._cache = {}

    def memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def platform(self, socket: str, mem: str, heavy: bool, old: bool = False):
        """(плата, память, SSD) — самые дешёвые под сокет и тип памяти."""
        def ram(cap):
            enough = lambda x: x["type"] == mem and x["cap"] >= cap
            return (self.task != "office" and cheapest(self.ram, lambda x: enough(x)
                                                       and (x.get("freq") or 0) >= RAM_FREQ.get(mem, 0))) \
                or cheapest(self.ram, enough)

        def pick():
            r = ram(self.need["ram"])
            # Нужный объём сейчас непомерно дорог (64 ГБ DDR4 за 80 тыс.) — вдвое меньше, но не меньше 16 ГБ:
            # иначе память съедает бюджет видеокарты и процессора
            if self.need["ram"] >= 32 and (not r or r["price"] > RAM_SHARE * self.budget):
                r = ram(self.need["ram"] // 2) or r
            if not r:
                return None
            m = cheapest(self.mb, lambda x: x["socket"] == socket and x["mem"] == mem
                         and (not x.get("memMax") or x["memMax"] >= r["cap"])
                         and not (heavy and x.get("chipset") in ENTRY_CHIPSETS)
                         and not (old and x.get("chipset") in NEW_AM4))
            if not m:
                return None
            fits = lambda x: x["cap"] >= self.need["ssd"] * 0.9 and not (
                x.get("form", "").startswith("M.2") and m.get("m2") == 0)
            fast = lambda x: fits(x) and re.search(r"nvme|pci", x.get("iface") or "", re.I)
            s = (self.task != "office" and cheapest(self.ssd, fast)) or cheapest(self.ssd, fits)
            return (m, r, s) if s else None
        return self.memo(("plat", socket, mem, heavy, old), pick)

    def chassis(self, form: str, g: dict | None, cls: int):
        """(кулер, корпус) с наименьшей суммой: низкий кулер открывает более дешёвые корпуса."""
        glen, gslots = (g.get("len") or 0, g.get("slots") or 0) if g else (0, 0)

        def pick():
            best = None
            for k in pareto([{**x, "low": -x["h"]} for x in self.cooler if cooler_ok(x, cls)], ("low",)):
                c = cheapest(self.case, lambda x: form in x["forms"] and x["maxGpu"] >= glen
                             and x["slots"] >= gslots and x["maxCooler"] >= k["h"])
                if c and (not best or k["price"] + c["price"] < best[0]["price"] + best[1]["price"]):
                    best = (k, c)
            return best
        return self.memo(("chassis", form, glen, gslots, cls), pick)

    def power(self, need: int, c: dict):
        def pick():
            fits = lambda x: x["watt"] >= need and (c["style"] != "sff" or x.get("form") == "SFX") and not (
                x.get("len") and c.get("maxPsu") and x["len"] > c["maxPsu"])
            trusted = lambda x: fits(x) and x.get("vendor") in TRUSTED_PSU
            good = lambda x: trusted(x) and CERTIFIED.search(x.get("cert") or "")
            return ((self.budget >= 70000 and cheapest(self.psu, good)) or cheapest(self.psu, trusted)
                    or cheapest(self.psu, fits))
        return self.memo(("psu", need, c["style"], c.get("maxPsu")), pick)

    def assemble(self, p: dict, g: dict | None, mem: str) -> dict | None:
        if not g and not p.get("igpu"):
            return None
        plat = self.platform(p["socket"], mem, heavy_cpu(p), old_am4(p))
        if not plat:
            return None
        m, r, s = plat
        ch = self.chassis(m["form"], g, cooler_class(p))
        if not ch:
            return None
        k, c = ch
        need, est = psu_need(g, p)
        w = self.power(need, c)
        if not w:
            return None
        parts = {"cpu": p, "gpu": g, "mb": m, "ram": r, "ssd": s, "cooler": k, "case": c, "psu": w}
        total = sum(x["price"] for x in parts.values() if x)
        return {"parts": parts, "ram_qty": 1, "total": total, "psu_need": need, "psu_est": est,
                "score": utility(self.task, self.res, g, p, r["type"])}

    def builds(self) -> list[dict]:
        out = []
        for p in self.cpus:
            mems = {m["mem"] for m in self.mb if m["socket"] == p["socket"]}
            for g in self.gpus:
                for mem in mems:
                    if (b := self.assemble(p, g, mem)) and b["total"] <= self.budget * PLUS_BUDGET:
                        out.append(b)
        return out


# ---------------------------------------------------------------- объяснение

def plural(n, one, few, many):
    n = abs(n) % 100
    return few if 2 <= n % 10 <= 4 and not 12 <= n <= 14 else one if n % 10 == 1 and n != 11 else many


def rub(x) -> str:
    return f"{round(x):,}".replace(",", " ") + " ₽"


def game_fps(b: dict, res: int) -> list[dict]:
    g, p, r = b["parts"]["gpu"], b["parts"]["cpu"], b["parts"]["ram"]
    k = perf.mem_k(p.get("socket"), r["type"])
    return [{"game": gm["name"], **perf.fps(gm, res, g["perf"], g.get("mem"), p["perf"], memk=k)} for gm in perf.GAMES]


CHANGE = {"cpu": ("процессор", lambda x: x["model"]),
          "mb": ("плата", lambda x: f"{x['socket']} {x.get('chipset') or ''} {x['mem']}".replace("  ", " ")),
          "gpu": ("видеокарта", lambda x: f"{x['chip']} {x.get('mem') or '?'} ГБ"),
          "ram": ("память", lambda x: f"{x['cap']} ГБ {x['type']}"), "psu": ("блок питания", lambda x: f"{x['watt']} Вт")}


def changes(a: dict, b: dict, table: dict = CHANGE) -> list[str]:
    """Что меняется от сборки a к b: «видеокарта GTX 1070 → RTX 2060 Super»."""
    out = []
    for k, (label, name) in table.items():
        x, y = a["parts"][k], b["parts"][k]
        if (x and name(x)) != (y and name(y)):
            # процессор слабее прежнего — деньги ушли в видеокарту
            weaker = k == "cpu" and x and y and y["perf"] < x["perf"]
            out.append(f"{label} {name(x) if x else 'нет'} → {name(y) if y else 'нет'}" + (" (проще)" if weaker else ""))
    return out


def upgrade(b: dict, adv: Advisor, pool: list[dict]) -> dict | None:
    """Доплата: самая дешёвая сборка дороже бюджета, но быстрее этой хотя бы на 10 %.
    Применяется подбором заново с бюджетом = её цене (оптимальная там не хуже)."""
    up = min((x for x in pool if x["score"] >= b["score"] * 1.1 and x["total"] > max(b["total"], adv.budget)),
             key=lambda x: x["total"], default=None)
    if not up:
        return None
    return {"budget": up["total"], "delta": up["total"] - b["total"],
            "gain": round((up["score"] / b["score"] - 1) * 100), "changes": changes(b, up)}


def explain(b: dict, adv: Advisor) -> dict:
    """Почему выбрана каждая деталь."""
    P = b["parts"]
    p, g, m, r, s, k, c, w = (P[x] for x in ("cpu", "gpu", "mb", "ram", "ssd", "cooler", "case", "psu"))
    why = {}
    if adv.task == "game":
        f = game_fps(b, adv.res)
        n_cpu = sum(x["limit"] == "cpu" for x in f)
        why["cpu"] = (f"Упор в процессор в {n_cpu} из {len(f)} игр — сильнее процессор даст больше кадров"
                      if n_cpu > len(f) / 2 else
                      f"Процессор не сдерживает видеокарту в {len(f) - n_cpu} из {len(f)} игр"
                      f" (игровой балл {p['perf']}, Ryzen 5 5600 = 100)")
    elif adv.task == "work":
        why["cpu"] = (f"{p.get('cores') or '?'} ядер / {p.get('threads') or '?'} потоков, многопоток {p['mt']} "
                      f"(Ryzen 5 5600 = 100) — главное для рендера и сборки")
    else:
        why["cpu"] = f"Встроенная графика — видеокарта не нужна; скорости ({p['perf']}) хватает для офиса"
    if g:
        why["gpu"] = f"{g['chip']} {g['mem'] or '?'} ГБ — балл {g['perf']} (RTX 4060 = 100)"
        stronger = [x for x in adv.gpus if x and x["perf"] > g["perf"]]
        if stronger:
            n = min(stronger, key=lambda x: x["price"])
            why["gpu"] += f"; следующая ступень — {n['chip']} за +{rub(n['price'] - g['price'])}"
    why["mb"] = (f"Самая дешёвая плата известного бренда {m['socket']} под {m['mem']} ({m['form']})" +
                 (f"; чипсет {m.get('chipset')} — питание для мощного процессора" if heavy_cpu(p) else ""))
    why["ram"] = f"{r['cap']} ГБ (2 × {r['cap'] // 2}) {r['type']} — двухканальный режим"
    why["ssd"] = f"{s['cap']} ГБ {s.get('iface') or ''}".strip()
    cls = cooler_class(p)
    why["cooler"] = (f"Теплопакет {'' if p.get('tdp') else '≈ '}{cpu_tdp(p)} Вт → " +
                     ("двухбашенный" if cls == 2 else "башенный" if cls == 1 else "подойдёт любой") +
                     f"; высота {k['h']} мм")
    why["case"] = (f"Вмещает плату {m['form']}" + (f", видеокарту {g['len']} мм (место {c['maxGpu']})" if g and g.get('len')
                   else "") + f", кулер {k['h']} мм (место {c['maxCooler']})")
    why["psu"] = (f"{w['watt']} Вт при нужных {b['psu_need']} Вт — " +
                  ("оценка: видеокарта + процессор + 100 Вт, запас 20 %" if b["psu_est"]
                   else "рекомендация производителя видеокарты"))
    return why


def advise(cat: dict, budget: int, task: str = "game", res: int = 1, **prefs) -> dict:
    adv = Advisor(cat, budget, task, res, **prefs)
    pool = adv.builds()
    fit = [b for b in pool if b["total"] <= budget]
    if not fit:
        return {"variants": [], "error": f"В бюджет {rub(budget)} совместимая сборка не собирается"}
    best = max(fit, key=lambda b: (b["score"], -b["total"]))
    variants = [("Оптимальная", best)]
    econ = min((b for b in fit if b["score"] >= best["score"] * ECON_SHARE), key=lambda b: b["total"])
    if econ["total"] <= best["total"] * 0.92:
        variants.insert(0, ("Экономная", econ))
    plus = max(pool, key=lambda b: (b["score"], -b["total"]))
    if plus["score"] >= best["score"] * PLUS_GAIN and plus["total"] > budget:
        variants.append(("С запасом", plus))
    out = []
    for label, b in variants:
        v = {"label": label, "total": b["total"], "score": round(b["score"], 1),
             "parts": {k: x for k, x in b["parts"].items() if x}, "why": explain(b, adv),
             # у экономной доплата — это оптимальная, она и так рядом: вместо доплаты — сравнение
             "upgrade": None if label == "Экономная" else upgrade(b, adv, pool),
             "compare": (f"На {rub(best['total'] - b['total'])} дешевле оптимальной, "
                         f"{round(b['score'] / best['score'] * 100)} % её производительности"
                         if label == "Экономная" else None),
             "problems": check({**b["parts"], "ram_qty": b["ram_qty"]})}
        if task == "game":
            v["fps"] = game_fps(b, res)
        out.append(v)
    return {"budget": budget, "task": task, "res": perf.RES[res], "variants": out,
            "searched": {"cpu": len(adv.cpus), "gpu": len(adv.gpus), "builds": len(pool)}}


# ---------------------------------------------------------------- текущая сборка: сводка, замена, улучшение

PART_CATS = ("cpu", "gpu", "mb", "ram", "ssd", "cooler", "case", "psu")
RESALE = 0.5            # б/у деталь продаётся примерно за половину цены новой
RESOLD = ("cpu", "gpu", "mb", "ram")     # что обычно продают при апгрейде
UP_CHANGE = {**CHANGE, "cooler": ("кулер", lambda x: x["name"][:32]), "case": ("корпус", lambda x: x["name"][:32])}
_index: dict = {}


def index(cat: dict) -> dict:
    """id → деталь по всему каталогу (кэш на объект каталога)."""
    if id(cat) not in _index:
        _index.clear()
        _index[id(cat)] = {x["id"]: x for c in PART_CATS + ("hdd",) for x in cat.get(c, [])}
    return _index[id(cat)]


def resolve_drives(cat: dict, drives=()) -> list[dict]:
    """Дополнительные накопители сборки (SSD и HDD) по id."""
    ix, own = index(cat), {x["id"] for c in DRIVE_CATS for x in cat.get(c, [])}
    for i in drives or ():
        if i not in own:
            raise KeyError(f"накопителя {i} нет в каталоге — он обновился" if i not in ix else f"{i} — не накопитель")
    return [ix[i] for i in drives or ()]


def resolve(cat: dict, ids: dict) -> dict:
    """Детали сборки по id. Видеокарты может не быть; остального — KeyError с понятным текстом."""
    ix, parts = index(cat), {}
    for k in PART_CATS:
        i = ids.get(k)
        if not i and k == "gpu":
            parts[k] = None
        elif not i:
            raise KeyError(f"в сборке нет детали «{k}»")
        elif i not in ix:
            raise KeyError(f"детали {i} нет в каталоге — он обновился")
        else:
            parts[k] = ix[i]
    return parts


def fps_table(parts: dict, ram_qty: int = 1) -> list[dict]:
    """FPS по играм во всех разрешениях."""
    g, p = parts.get("gpu"), parts["cpu"]
    if not (g and g.get("perf") and p.get("perf")):
        return []
    dual = (parts["ram"].get("kit") or 1) * ram_qty >= 2
    k = perf.mem_k(p.get("socket"), parts["ram"].get("type"))
    rows = []
    for gm in perf.GAMES:
        r = [perf.fps(gm, i, g["perf"], g.get("mem"), p["perf"], dual, k) for i in range(len(perf.RES))]
        rows.append({"id": gm["id"], "game": gm["name"], "fps": [x["fps"] for x in r],
                     "limit": [x["limit"] for x in r], "vram": [x["vram"] for x in r]})
    return rows


def build_info(cat: dict, ids: dict, ram_qty: int = 1, drives=()) -> dict:
    """Сводка сборки: детали, дополнительные накопители, итог, проверки совместимости, FPS."""
    parts, ext = resolve(cat, ids), resolve_drives(cat, drives)
    ch = checks({**parts, "drives": ext, "ram_qty": ram_qty})
    total = sum(x["price"] for x in parts.values() if x) + parts["ram"]["price"] * (ram_qty - 1) + \
        sum(d["price"] for d in ext)
    room = drive_room(parts["case"] and case_view(parts["case"]), parts.get("mb"),
                      [d for d in (parts.get("ssd"), *ext) if d])
    return {"parts": parts, "drives": ext, "drive_room": room, "total": total, "ok": all(c["ok"] for c in ch),
            "checks": ch, "fps": fps_table(parts, ram_qty), "res": perf.RES}


def spec(part: str, x: dict) -> str:
    """Короткая строка характеристик для списка замен."""
    g = x.get
    f = {"cpu": (g("socket"), g("pack"), g("cores") and f"{g('cores')} ядер", g("perf") and f"игры {g('perf')}",
                 g("mt") and f"многопоток {g('mt')}"),
         "gpu": (g("chip"), g("mem") and f"{g('mem')} ГБ", g("len") and f"{g('len')} мм",
                 g("perf") and f"балл {g('perf')}", g("article")),     # артикул — различает одноимённые версии
         "mb": (g("socket"), g("chipset"), g("form"), g("mem")),
         "ram": (g("cap") and f"{g('cap')} ГБ", g("type") and f"{g('type')}-{g('freq') or '?'}",
                 f"модулей {g('kit') or 1}"),
         "ssd": (g("cap") and f"{g('cap')} ГБ", g("form"), g("iface")),
         "hdd": (g("cap") and f"{g('cap')} ГБ", "HDD 3,5″", g("rpm") and f"{g('rpm')} об/мин"),
         "cooler": ("двухбашенный" if g("type") == "tower" and (g("towers") or 1) >= 2 else
                    "башенный" if g("type") == "tower" else "низкий", g("h") and f"{g('h')} мм"),
         "case": (", ".join(g("forms") or []), g("maxGpu") and f"видеокарта до {g('maxGpu')} мм"),
         "psu": (g("watt") and f"{g('watt')} Вт", g("form"), g("cert") or "без сертификата")}[part]
    return " · ".join(str(v) for v in (*f, COLOR.get(g("color"))) if v)     # цвет — различает одноимённые


def search_re(word: str) -> re.Pattern:
    """Слово поиска: без регистра, пробелы и дефисы внутри не важны («LGA 1700» = «lga1700», «DDR-5» = «ddr5»);
    слово — с начала: число не с середины числа («1700» не находит «i7-11700»), буквы — не с середины
    слова («ti» не находит «Edition»)."""
    w = re.sub(r"[\s\-_]+", "", word.lower().replace("ё", "е"))
    start = r"(?<!\d)" if w[:1].isdigit() else r"(?<![a-zа-я])" if w[:1].isalpha() else ""
    return re.compile(start + r"[\s\-_]*".join(map(re.escape, w)))


def search_text(code: str, x: dict) -> str:
    """Где ищет поиск в списке замен: название, строка характеристик и текстовые поля записи —
    сокет, чипсет, чип, тип, форм-фактор, интерфейс (в названиях товаров их часто нет)."""
    fields = (v for k, v in x.items() if isinstance(v, str) and k not in ("id", "url", "img"))
    return " ".join((x["name"], spec(code, x), *fields, *(x.get("forms") or []))).lower().replace("ё", "е")


# ---------------------------------------------------------------- фильтры списка замен

def gb(v):
    return f"{v / 1000:g} ТБ" if v >= 1000 else f"{v} ГБ"


COLOR = {"black": "чёрный", "white": "белый", "pink": "розовый"}
# (ключ, подпись, значение детали — одно или список, вид, подпись значения). set — галочки, range — от/до.
# Порядок — порядок в панели; цена — всегда первой, производитель и магазин — последними.
FACETS = {
    "cpu": [("socket", "Сокет", lambda x: x.get("socket"), "set", None),
            ("cores", "Ядер", lambda x: x.get("cores"), "set", None),
            ("igpu", "Встроенная графика", lambda x: "есть" if x.get("igpu") else "нет", "set", None),
            ("pack", "Комплектация", lambda x: x.get("pack"), "set", None)],
    "gpu": [("chip", "Чип", lambda x: x.get("chip"), "set", None),
            ("mem", "Видеопамять", lambda x: x.get("mem"), "set", gb),
            ("len", "Длина, мм", lambda x: x.get("len"), "range", None),
            ("color", "Цвет", lambda x: x.get("color"), "set", COLOR.get)],
    "mb": [("socket", "Сокет", lambda x: x.get("socket"), "set", None),
           ("chipset", "Чипсет", lambda x: x.get("chipset"), "set", None),
           ("form", "Форм-фактор", lambda x: x.get("form"), "set", None),
           ("mem", "Тип памяти", lambda x: x.get("mem"), "set", None),
           ("memSlots", "Слотов памяти", lambda x: x.get("memSlots"), "set", None),
           ("wifi", "Wi-Fi", lambda x: f"Wi-Fi {x['wifi']}" if x.get("wifi") else "нет", "set", None)],
    "ram": [("type", "Тип", lambda x: x.get("type"), "set", None),
            ("freq", "Частота, МГц", lambda x: x.get("freq"), "set", None),
            ("cap", "Объём комплекта", lambda x: x.get("cap"), "set", gb),
            ("module", "Объём модуля", lambda x: x.get("cap") and x["cap"] // (x.get("kit") or 1), "set", gb),
            ("kit", "Модулей в комплекте", lambda x: x.get("kit") or 1, "set", None),
            ("cl", "Тайминги (CL)", lambda x: x.get("cl"), "set", None)],
    "ssd": [("cap", "Объём", lambda x: x.get("cap"), "set", gb),
            ("form", "Форм-фактор", lambda x: x.get("form"), "set", lambda v: "2,5″" if v == "2.5" else v),
            ("iface", "Интерфейс", lambda x: x.get("iface"), "set", None)],
    "hdd": [("cap", "Объём", lambda x: x.get("cap"), "set", gb),
            ("rpm", "Обороты", lambda x: x.get("rpm"), "set", lambda v: f"{v} об/мин")],
    "drive": [("kind", "Тип", lambda x: "HDD" if x.get("form") == "3.5" else "SSD", "set", None),
              ("cap", "Объём", lambda x: x.get("cap"), "set", gb)],
    "cooler": [("type", "Тип", lambda x: "двухбашенный" if (x.get("towers") or 1) >= 2 else
                {"tower": "башенный", "low": "низкопрофильный"}.get(x.get("type")), "set", None),
               ("h", "Высота, мм", lambda x: x.get("h"), "range", None),
               ("color", "Цвет", lambda x: x.get("color"), "set", COLOR.get)],
    "case": [("forms", "Влезает плата", lambda x: x.get("forms"), "set", None),
             ("maxGpu", "Видеокарта до, мм", lambda x: x.get("maxGpu"), "range", None),
             ("maxCooler", "Кулер до, мм", lambda x: x.get("maxCooler"), "range", None),
             ("color", "Цвет", lambda x: x.get("color"), "set", COLOR.get)],
    "psu": [("watt", "Мощность, Вт", lambda x: x.get("watt"), "range", None),
            ("cert", "Сертификат 80 PLUS", lambda x: x.get("cert"), "set", None),
            ("modular", "Кабели", lambda x: x.get("modular"), "set",
             {"full": "отстёгиваются все", "semi": "частично", "no": "несъёмные"}.get),
            ("form", "Форм-фактор", lambda x: x.get("form"), "set", None)],
}
COMMON_FACETS = ([("price", "Цена, ₽", lambda x: x["price"], "range", None)],
                 [("vendor", "Производитель", lambda x: x.get("vendor"), "set", None),
                  ("shop", "Магазин", lambda x: x.get("shops"), "set", None)])


def facet_defs(part: str) -> list:
    return COMMON_FACETS[0] + FACETS.get(part, []) + COMMON_FACETS[1]


def facet_vals(get, x) -> list:
    v = get(x)
    return [] if v is None or v == "" else list(v) if isinstance(v, (list, tuple)) else [v]


def passes(x, flt: dict, defs: list, skip: str | None = None) -> bool:
    """Деталь проходит фильтры: set — любое из отмеченных значений, range — в пределах [от, до]."""
    for key, _, get, kind, _ in defs:
        f = flt.get(key)
        if key == skip or not f:
            continue
        vals = facet_vals(get, x)
        if kind == "set":
            if not any(str(v) in f for v in vals):
                return False
        else:
            lo, hi = (list(f) + [None, None])[:2]
            if not vals or lo is not None and vals[0] < lo or hi is not None and vals[0] > hi:
                return False
    return True


def facets(rows: list, flt: dict, defs: list) -> list:
    """Панель фильтров: у каждого — значения со счётчиками по деталям, прошедшим остальные фильтры
    (как в интернет-магазинах: видно, сколько останется, если отметить)."""
    out = []
    for key, label, get, kind, fmt in defs:
        xs = [x for x in rows if passes(x, flt, defs, skip=key)]
        if kind == "set":
            cnt = Counter(str(v) for x in xs for v in facet_vals(get, x))
            raw = {str(v): v for x in xs for v in facet_vals(get, x)}
            for v in flt.get(key) or []:          # отмеченное остаётся видно, даже если с ним ничего не нашлось
                cnt.setdefault(v, 0)
                raw.setdefault(v, v)
            if len(cnt) < 2 and not flt.get(key):
                continue                          # выбирать не из чего
            num = all(isinstance(v, (int, float)) for v in raw.values())
            vals = sorted(cnt, key=lambda k: raw[k] if num else (-cnt[k], k.lower()))
            out.append({"key": key, "label": label, "kind": kind,
                        "values": [{"v": k, "label": str(fmt(raw[k]) or k) if fmt else k, "n": cnt[k]} for k in vals]})
        else:
            nums = [facet_vals(get, x)[0] for x in xs if facet_vals(get, x)]
            if nums and (min(nums) < max(nums) or flt.get(key)):
                out.append({"key": key, "label": label, "kind": kind, "min": min(nums), "max": max(nums)})
    return out


def options(cat: dict, ids: dict, part: str, q: str = "", limit: int = 80, ram_qty: int = 1,
            drives=(), drive: int | None = None, sort: str = "fit", filters: dict | None = None,
            fit_only: bool = False) -> dict:
    """Чем заменить деталь: текущая и подходящие — первыми, внутри — по цене; у неподходящих — причина.
    part="drive" — дополнительный накопитель (SSD и HDD вместе): drive — какой заменить, None — добавить.
    sort: fit — как выше; asc/desc — подходящие по цене вверх/вниз, неподходящие — в конце.
    filters — {ключ фильтра: [значения]} или {ключ: [от, до]} (FACETS), fit_only — только подходящие.
    facets — панель фильтров для найденного по q."""
    cur, ext = resolve(cat, ids), resolve_drives(cat, drives)
    words = [search_re(w) for w in q.split()]
    out, rows = [], []
    for code in DRIVE_CATS if part == "drive" else (part,):
        for x in cat.get(code, []):
            if not x.get("price"):
                continue
            if part == "drive":
                d2, i = list(ext), drive if drive is not None and drive < len(ext) else len(ext)
                d2[i:i + 1] = [x]
                b, current = {**cur, "drives": d2}, i < len(ext) and ext[i]["id"] == x["id"]
            else:
                b, current = {**cur, part: x, "drives": ext}, x["id"] == ids.get(part)
            bad = check({**b, "ram_qty": ram_qty})
            rows.append((x, code, current, bad, not words or all(w.search(search_text(code, x)) for w in words)))
    # Проблема, которая есть при любом варианте (слабый кулер при замене платы), — не про эту деталь:
    # её показывает сборка, а вариант из-за неё не должен уходить в «неподходящие».
    common_bad = set.intersection(*(set(r[3]) for r in rows)) if rows else set()
    flt, defs = filters or {}, facet_defs(part)
    found = []
    for x, code, current, bad, hit in rows:
        if not hit:
            continue
        bad = [m for m in bad if m not in common_bad]
        if fit_only and bad and not current:
            continue
        found.append(x)
        if not passes(x, flt, defs):
            continue
        out.append({"id": x["id"], "name": x["name"], "price": x["price"], "shops": x.get("shops", []),
                    "url": x.get("url"), "links": x.get("links", {}), "offers": x.get("offers", 0),
                    "spec": spec(code, x), "problem": bad[0] if bad else None, "current": current,
                    "soft": bool(bad) and not bad[0].startswith(("Сокет", "Чипсет"))})
    # Сначала подходящие, потом те, что подойдут после замены другой детали (soft: память не того типа),
    # в конце — несовместимые с процессором (сокет, чипсет)
    grade = lambda o: 0 if o["problem"] is None else 1 if o["soft"] else 2
    if sort == "fit":
        out.sort(key=lambda o: (not o["current"], grade(o), o["offers"] < MIN_OFFERS, o["price"]))
    else:
        sign = -1 if sort == "desc" else 1
        out.sort(key=lambda o: (grade(o), sign * o["price"]))
    return {"items": out[:limit], "found": len(out), "facets": facets(found, flt, defs)}


def refit(adv: Advisor, cur: dict, p: dict, g: dict | None) -> dict | None:
    """Сборка cur с процессором p и видеокартой g. Остальное — прежнее; что перестало подходить,
    меняется на самое дешёвое подходящее: плата под сокет (память — прежняя, если можно), кулер,
    корпус, блок питания. None — если так не собрать."""
    b, heavy, old = {**cur, "cpu": p, "gpu": g}, heavy_cpu(p), old_am4(p)
    fits = lambda x: x["socket"] == p["socket"] and not (heavy and x.get("chipset") in ENTRY_CHIPSETS) \
        and not (old and x.get("chipset") in NEW_AM4)
    if not fits(b["mb"]):
        if m := cheapest(adv.mb, lambda x: fits(x) and x["mem"] == b["ram"]["type"]):
            b["mb"] = m
        else:
            plat = next((pl for mem in sorted({x["mem"] for x in adv.mb if fits(x)})
                         if (pl := adv.platform(p["socket"], mem, heavy, old))), None)
            if not plat:
                return None
            b["mb"], b["ram"] = plat[0], plat[1]
    c, cls = case_view(b["case"]), cooler_class(p)
    if not cooler_ok(b["cooler"], cls):
        if not (k := cheapest(adv.cooler, lambda x: cooler_ok(x, cls) and x["h"] <= c["maxCooler"])):
            return None
        b["cooler"] = k
    glen, gslots = (g.get("len") or 0, g.get("slots") or 0) if g else (0, 0)
    fits_case = lambda x: (b["mb"]["form"] in x["forms"] and x["maxGpu"] >= glen and x["slots"] >= gslots
                           and x["maxCooler"] >= b["cooler"]["h"])
    if not fits_case(c):
        if not (c := cheapest(adv.case, fits_case)):
            return None
        b["case"] = c
    need, _ = psu_need(g, p)
    w = b["psu"]
    if w["watt"] < need or (c["style"] == "sff" and w.get("form") != "SFX") or \
            (w.get("len") and c.get("maxPsu") and w["len"] > c["maxPsu"]):
        if not (w := adv.power(need, c)):
            return None
        b["psu"] = w
    return None if check(b) else b


# ---------------------------------------------------------------- улучшения «не про FPS»

MESH = re.compile(r"mesh|air ?flow|сетк|сетчат|(?<![a-z])air(?![a-z])", re.I)    # продуваемый фронт — по названию
GOLD = re.compile(r"gold|platinum|titanium", re.I)
NVME = re.compile(r"nvme|pci", re.I)
Q_CHANGE = {**UP_CHANGE, "ssd": ("SSD", lambda x: f"{x['cap']} ГБ {x.get('iface') or ''}".strip()),
            "psu": ("блок питания", lambda x: x["name"][:32])}


def cap_label(gb: int) -> str:
    return f"{gb / 1000:g} ТБ" if gb >= 1000 else f"{gb} ГБ"


def to_ddr5(adv: Advisor, cur: dict, ram_qty: int = 1) -> dict | None:
    """Та же сборка на DDR5: самые дешёвые плата под тот же сокет и комплект DDR5 того же объёма (больше
    объём — отдельное улучшение, вместе выходит дороже любой доплаты); процессор прежний.
    None — если сборка на DDR4 не переводится."""
    p, r0 = cur["cpu"], cur["ram"]
    if r0["type"] != "DDR4":
        return None
    r5 = cheapest(adv.ram, lambda x: x["type"] == "DDR5" and x["cap"] >= max(16, r0["cap"] * ram_qty)
                  and (x.get("freq") or 0) >= RAM_FREQ["DDR5"])
    heavy = heavy_cpu(p)
    for m in sorted((m for m in adv.mb if m["socket"] == p["socket"] and m["mem"] == "DDR5"
                     and not (heavy and m.get("chipset") in ENTRY_CHIPSETS)), key=lambda m: m["price"]) if r5 else []:
        if not check({**cur, "mb": m, "ram": r5, "ram_qty": 1}):
            return {**cur, "mb": m, "ram": r5}
    return None


def quality_upgrades(adv: Advisor, cur: dict, money: int, resale: bool = False, ram_qty: int = 1,
                     skip: set = frozenset()) -> list[dict]:
    """Улучшения, которых FPS-модель не видит (или прирост по ней мал): SSD, объём памяти, переход на DDR5,
    кулер, продуваемый корпус, блок питания. По одному на вид — самый дешёвый вариант, который даёт
    улучшение и оставляет всю сборку совместимой; цена — с продажей старых платы и памяти, если resale.
    skip — виды, уже показанные среди улучшений FPS."""
    p, g = cur["cpu"], cur["gpu"]
    out = []

    def offer(kind: str, title: str, benefit: str, **new) -> bool:
        b = {**cur, **new}
        gross = sum(x["price"] for x in new.values())
        back = round(RESALE * sum(cur[k]["price"] for k in new if k in RESOLD)) if resale else 0
        if gross - back > money or check({**b, "ram_qty": 1 if "ram" in new else ram_qty}):
            return False
        out.append({"kind": kind, "title": title, "benefit": benefit, "cost": gross - back, "gross": gross,
                    "resale": back, "changes": changes({"parts": cur}, {"parts": b}, Q_CHANGE),
                    "ids": {k: x["id"] for k, x in b.items() if x}})
        return True

    def cheapest_ok(kind, part, items, pred, title, benefit):
        for x in sorted((x for x in items if pred(x) and x["id"] != cur[part]["id"]), key=lambda x: x["price"]):
            if offer(kind, title(x), benefit(x), **{part: x}):
                return

    # SSD: больше места и/или NVMe вместо SATA; старый можно оставить вторым диском
    s0 = cur["ssd"]
    if s0["cap"] < 1000:
        cheapest_ok("ssd", "ssd", adv.ssd, lambda x: x["cap"] >= max(1000, 2 * s0["cap"]) and NVME.search(x.get("iface") or ""),
                    lambda x: f"SSD → {cap_label(x['cap'])} NVMe",
                    lambda x: f"В {x['cap'] / s0['cap']:.0f} раза больше места" +
                              ("; NVMe вместо SATA — игры и система грузятся быстрее" if not NVME.search(s0.get("iface") or "") else "") +
                              ". Старый можно оставить вторым диском")
    elif not NVME.search(s0.get("iface") or ""):
        cheapest_ok("ssd", "ssd", adv.ssd, lambda x: x["cap"] >= s0["cap"] and NVME.search(x.get("iface") or ""),
                    lambda x: f"SSD → {cap_label(x['cap'])} NVMe",
                    lambda x: "NVMe вместо SATA: ~3 500–7 000 МБ/с против ~550 — игры и система грузятся быстрее")

    # Память: объём, затем переход на DDR5 (плата + память; процессор тот же)
    r0 = cur["ram"]
    if r0["cap"] * ram_qty < 32:
        cheapest_ok("ram", "ram", adv.ram, lambda x: x["type"] == r0["type"] and x["cap"] >= 32
                    and (x.get("freq") or 0) >= RAM_FREQ.get(x["type"], 0),
                    lambda x: f"Память → {x['cap']} ГБ {x['type']}",
                    lambda x: f"{x['cap']} ГБ — меньше подтормаживаний в тяжёлых играх (Tarkov, STALKER 2) "
                              f"и запас под браузер и стрим на фоне; средний FPS почти не меняется, "
                              f"{r0['cap'] * ram_qty} ГБ уже впритык")
    if "ddr5" not in skip and (b5 := to_ddr5(adv, cur, ram_qty)):
        k = perf.mem_k(p.get("socket"), "DDR4")
        offer("ddr5", f"Память → DDR5 {b5['ram']['cap']} ГБ (плата + память)",
              f"Переход на DDR5-{b5['ram'].get('freq')}: платформа на будущее" +
              (f"; процессор перестаёт терять ≈ {round((1 - k) * 100)} % в играх с упором в него"
               if k < 1 else ""),
              mb=b5["mb"], ram=b5["ram"])

    # Кулер: низкий → башня; одиночная башня → двухбашенный, только если процессор горячий
    k0, tdp = cur["cooler"], cpu_tdp(p)
    if k0.get("type") != "tower":
        cheapest_ok("cooler", "cooler", adv.cooler, lambda x: x.get("type") == "tower",
                    lambda x: f"Кулер → {x['name'][:40]}",
                    lambda x: f"Башенный вместо низкого: процессор {tdp} Вт будет холоднее, вентилятор — тише")
    elif (k0.get("towers") or 1) < 2 and tdp >= 125:
        cheapest_ok("cooler", "cooler", adv.cooler, lambda x: x.get("type") == "tower" and (x.get("towers") or 1) >= 2,
                    lambda x: f"Кулер → {x['name'][:40]}",
                    lambda x: f"Двухбашенный: запас по теплу для процессора {tdp} Вт — "
                              f"тише под нагрузкой и без сброса частот")

    # Корпус: сетчатая передняя панель
    if not MESH.search(cur["case"]["name"]):
        cheapest_ok("case", "case", adv.case, lambda x: MESH.search(x["name"]),
                    lambda x: f"Корпус → {x['name'][:40]}",
                    lambda x: "Сетчатая передняя панель (по названию модели) — больше воздуха к видеокарте "
                              "и процессору, ниже температура и шум")

    # Блок питания: 80 PLUS Gold и выше от проверенного производителя
    w0, (need, _) = cur["psu"], psu_need(g, p)
    if not GOLD.search(w0.get("cert") or "") or w0.get("vendor") not in TRUSTED_PSU:
        cheapest_ok("psu", "psu", adv.psu, lambda x: GOLD.search(x.get("cert") or "") and x.get("vendor") in TRUSTED_PSU
                    and x["watt"] >= max(need, w0["watt"]),
                    lambda x: f"Блок питания → {x['name'][:40]}",
                    lambda x: f"80 PLUS {x['cert']} от проверенного производителя: КПД около 90 %, меньше нагрев "
                              f"и шум; {x['watt']} Вт при нужных {need}")
    return out


def upgrade_build(cat: dict, ids: dict, money: int, task: str = "game", res: int = 1,
                  ram_qty: int = 1, limit: int = 4, resale: bool = False, drives=()) -> dict:
    """«Улучшить сборку за N ₽»: какие детали заменить. Платит пользователь только за новые детали,
    остальное остаётся; resale — старые процессор/видеокарта/плата/память продаются за RESALE цены.
    Перебор: процессоры × видеокарты по Парето, недостающее — refit()."""
    cur = resolve(cat, ids)
    # дополнительные накопители остаются как есть — входят только в итоговую цену сборки
    total = sum(x["price"] for x in cur.values() if x) + sum(d["price"] for d in resolve_drives(cat, drives))
    p0, g0 = cur["cpu"], cur["gpu"]
    if not p0.get("perf") or (task == "game" and not (g0 and g0.get("perf"))):
        return {"suggestions": [], "error": "Для оценки нужны процессор и видеокарта с известной производительностью"}
    adv = Advisor(cat, total + money, task, res)
    base = utility(task, res, g0, p0, cur["ram"]["type"])
    gpus = [g0] + ([] if task == "office" else [g for g in adv.gpus if g and g["id"] != (g0 or {}).get("id")])
    cpus = [p0] + [p for p in adv.cpus if p["id"] != p0["id"]]
    cands = []
    for p in cpus:
        for g in gpus:
            if p is p0 and g is g0 or not (b := refit(adv, cur, p, g)):
                continue
            new = {k for k, x in b.items() if x and x["id"] != (cur[k] or {}).get("id")}
            gross = sum(b[k]["price"] for k in new)
            back = round(RESALE * sum(cur[k]["price"] for k in new & set(RESOLD) if cur[k])) if resale else 0
            if gross - back <= money and (s := utility(task, res, g, p, b["ram"]["type"])) >= base * 1.03:
                cands.append({"parts": b, "new": new, "cost": gross - back, "gross": gross, "back": back, "score": s})
    # DDR4 → DDR5 при том же процессоре и видеокарте: на LGA1700 это прирост FPS по модели
    if task == "game" and (b := to_ddr5(adv, cur, ram_qty)):
        gross = b["mb"]["price"] + b["ram"]["price"]
        back = round(RESALE * (cur["mb"]["price"] + cur["ram"]["price"])) if resale else 0
        if gross - back <= money and (s := utility(task, res, g0, p0, "DDR5")) >= base * 1.03:
            cands.append({"parts": b, "new": {"mb", "ram"}, "cost": gross - back, "gross": gross, "back": back,
                          "score": s})
    if not cands:
        return {"suggestions": [], "extras": quality_upgrades(adv, cur, money, resale, ram_qty), "total": total,
                "error": f"За {rub(money)} заметно (хотя бы на 3 %) поднять FPS не выйдет" +
                ("" if resale else " — попробуйте учесть продажу старых деталей")}

    def kind(c):
        n = c["new"]
        return "memory" if not n & {"cpu", "gpu"} else "gpu" if "cpu" not in n else \
            "cpu" if "gpu" not in n and "mb" not in n else "platform" if "gpu" not in n else "both"
    best = max(cands, key=lambda c: (c["score"], -c["cost"]))
    value = max(cands, key=lambda c: ((c["score"] / base - 1) / c["cost"], c["score"]))
    by_kind = {}
    for c in sorted(cands, key=lambda c: (-c["score"], c["cost"])):
        by_kind.setdefault(kind(c), c)
    picked = []
    for c, tag in [(best, "Максимум за эти деньги"), (value, "Выгоднее всего")] + [(c, None) for c in by_kind.values()]:
        if all(c is not x for x, _ in picked):
            picked.append((c, tag))
    out = []
    for c, tag in picked[:limit]:
        P, n = c["parts"], c["new"]
        mem = kind(c) == "memory"
        title = " + ".join([f"Процессор → {P['cpu']['model']}"] * ("cpu" in n) +
                           [f"Видеокарта → {P['gpu']['chip']} {P['gpu'].get('mem') or '?'} ГБ"] * ("gpu" in n)) or \
            (f"Память → DDR5 {P['ram']['cap']} ГБ (плата + память)" if mem else "Замена")
        s = {"title": title, "tag": tag, "cost": c["cost"], "gross": c["gross"], "resale": c["back"],
             "gain": round((c["score"] / base - 1) * 100),
             "changes": changes({"parts": cur}, {"parts": P}, UP_CHANGE),
             "ids": {k: x["id"] for k, x in P.items() if x}}
        if task == "game":
            # пример — Cyberpunk; у памяти он обычно упирается в видеокарту, поэтому игра с наибольшим приростом
            was, now = fps_table(cur, ram_qty), fps_table(P, 1 if "ram" in n else ram_qty)
            i = max(range(len(was)), key=lambda i: now[i]["fps"][res] / was[i]["fps"][res]) if mem else \
                next(i for i, r in enumerate(was) if r["id"] == "cyberpunk")
            s["fps"] = f"{was[i]['game']} в {perf.RES[res]}: {was[i]['fps'][res]} → {now[i]['fps'][res]} FPS"
        out.append(s)
    # переход на DDR5, попавший в улучшения FPS, второй раз в «не про FPS» не показывается
    shown = {"ddr5"} if any(kind(c) == "memory" for c, _ in picked[:limit]) else set()
    return {"suggestions": out, "extras": quality_upgrades(adv, cur, money, resale, ram_qty, shown), "total": total}


# ---------------------------------------------------------------- командная строка

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Подбор сборки ПК по бюджету и задаче")
    ap.add_argument("budget", type=int)
    ap.add_argument("--task", choices=TASKS, default="game")
    ap.add_argument("--res", choices=perf.RES, default="1440p")
    ap.add_argument("--cpu", choices=("AMD", "Intel"))
    ap.add_argument("--gpu", choices=("NVIDIA", "AMD", "Intel"))
    ap.add_argument("--form", choices=FORM_ORDER, help="наибольший форм-фактор платы")
    a = ap.parse_args()
    r = advise(load(), a.budget, a.task, perf.RES.index(a.res), cpu_vendor=a.cpu, gpu_vendor=a.gpu, max_form=a.form)
    if not r["variants"]:
        print(r["error"])
        return
    print(f"{TASKS[a.task]}, {rub(a.budget)}" + (f", {r['res']}" if a.task == "game" else "") +
          f" — перебрано сборок: {r['searched']['builds']} (CPU {r['searched']['cpu']}, GPU {r['searched']['gpu']})")
    for v in r["variants"]:
        u = v["upgrade"]
        print(f"\n■ {v['label']}: {rub(v['total'])}" + (f"  · {v['compare']}" if v["compare"] else "") +
              (f"  · доплата +{rub(u['delta'])} → +{u['gain']} %: {'; '.join(u['changes'])}" if u else ""))
        for k, x in v["parts"].items():
            print(f"  {k:6} {rub(x['price']):>10}  {x['name'][:52]:52}  {v['why'].get(k, '')}")
        if "fps" in v:
            print("  FPS: " + ", ".join(f"{x['game']} {x['fps']}" for x in v["fps"]))
        if v["problems"]:
            print("  !!! " + "; ".join(v["problems"]))


if __name__ == "__main__":
    main()
