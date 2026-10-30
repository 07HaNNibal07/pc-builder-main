"""Разведка: какие характеристики отдаёт карточка WB (card.json на basket-NN).

Берёт по несколько самых отзывных товаров WB в категориях gpu/case/cooler/mb,
находит корзину перебором и сохраняет карточки в recon/wb_cards.json,
а в консоль — названия характеристик с примерами значений.
Запуск (Windows): .venv-win/Scripts/python.exe recon/wb_card.py
"""
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.adapters.wb import HEADERS  # noqa: E402
from collector.db import connect  # noqa: E402

PER_CAT = 3
OUT = Path(__file__).with_name("wb_cards.json")

s = requests.Session()
s.headers.update(HEADERS)


def card(nm: int):
    vol, part = nm // 100000, nm // 1000
    for b in range(1, 41):
        url = f"https://basket-{b:02d}.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json"
        try:
            r = s.get(url, timeout=10)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            return b, r.json()
    return None, None


with connect() as db:
    rows = db.execute("""
        select c.code, o.external_id::bigint from offers o
        join marketplaces mp on mp.id=o.marketplace_id join categories c on c.id=o.category_id
        where mp.code='wb' and c.code in ('gpu','case','cooler','mb') and o.model_id is not null
        order by c.code, o.feedbacks desc nulls last""").fetchall()
picked = defaultdict(list)
for cat, nm in rows:
    if len(picked[cat]) < PER_CAT:
        picked[cat].append(nm)

cards, opts = {}, defaultdict(dict)
for cat, nms in picked.items():
    for nm in nms:
        t = time.monotonic()
        b, c = card(nm)
        print(f"{cat} {nm}: basket-{b} {time.monotonic() - t:.1f}s")
        if not c:
            continue
        cards[str(nm)] = {"cat": cat, "basket": b, "card": c}
        for o in c.get("options", []):
            opts[cat].setdefault(o["name"], o["value"])

OUT.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")
print("top-level keys:", sorted(next(iter(cards.values()))["card"].keys()) if cards else "-")
for cat, d in opts.items():
    print(f"\n== {cat} ({len(d)} полей)")
    for k, v in d.items():
        print(f"  {k}: {v}"[:110])
