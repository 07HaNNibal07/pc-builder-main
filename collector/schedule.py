"""Планировщик сбора: один запуск («тик») — одно задание площадка × категория.

    .venv-win\\Scripts\\python.exe -m collector.schedule            # один тик
    .venv-win\\Scripts\\python.exe -m collector.schedule --plan     # показать очередь
    .venv-win\\Scripts\\python.exe -m collector.schedule --mp ali   # AliExpress — только так

Раз в час его запускает Планировщик заданий Windows (scripts\\collect_tick.bat).
За тик берётся самое давнее задание среди площадок, которые сейчас можно трогать:
- у площадки свой срок обновления (PERIOD) и глубина выдачи (PAGES);
- после отказа по частоте (у WB — серия 429, у AliExpress — пустая выдача)
  площадка отдыхает COOLDOWN часов: короткие повторы лимит только продлевают
  (recon/RECON.md, «Лимит частоты WB»);
- неудачное по другой причине задание повторяется не раньше чем через RETRY
  часов, чтобы не занимать собой каждый тик;
- AliExpress сам не собирается: капчу там проходит человек.
После сбора: нормализация категории → характеристики с карточек новых
предложений → каталог для 3D. Два сборщика с Chrome рвут друг друга, поэтому
тик пропускается, если идёт предыдущий или сборщик запущен вручную.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collector.db import connect
from collector.run import ADAPTERS, collect

ROOT = Path(__file__).resolve().parent.parent
LOCK = Path(tempfile.gettempdir()) / "pcb-collect.lock"
LOCK_STALE = timedelta(hours=6)          # тик столько не длится — замок остался от сбоя

AUTO = ("wb", "dns", "ozon", "ym")
PAGES = {"wb": 3, "dns": 7, "ozon": 20, "ym": 8, "ali": 5}         # глубина по опыту прогонов
PERIOD = {"wb": 24, "dns": 24, "ozon": 24, "ym": 48, "ali": 72}    # часов между обновлениями
COOLDOWN = {"wb": 6, "ali": 12}          # часов отдыха после отказа по частоте, прочим — 3
RETRY = 3                                # часов до повтора неудачного задания
CARDS = ("wb", "dns", "ozon", "ym")      # площадки, чьи карточки умеет collector.cards

# Задания, для которых настроен раздел площадки (иначе collect() их пропускает
# без записи в журнал, и они вечно стояли бы первыми)
TARGETS = """
SELECT m.code, c.code,
       max(s.finished_at) FILTER (WHERE s.status IN ('ok', 'partial')),
       max(s.started_at)
FROM collect_targets t
JOIN marketplaces m ON m.id = t.marketplace_id
JOIN categories c   ON c.id = t.category_id
LEFT JOIN collect_sessions s ON s.marketplace_id = t.marketplace_id AND s.category_id = t.category_id
WHERE t.active AND EXISTS (SELECT 1 FROM marketplace_categories mc
                           WHERE mc.marketplace_id = t.marketplace_id AND mc.category_id = t.category_id)
GROUP BY 1, 2
"""

LIMITED = """
SELECT m.code, max(s.finished_at)
FROM collect_sessions s JOIN marketplaces m ON m.id = s.marketplace_id
WHERE s.status IN ('failed', 'partial') AND (s.error LIKE '%429%' OR s.error LIKE 'пустая выдача%')
GROUP BY 1
"""

LAST_STATUS = """
SELECT s.status FROM collect_sessions s
JOIN marketplaces m ON m.id = s.marketplace_id
JOIN categories c   ON c.id = s.category_id
WHERE m.code = %s AND c.code = %s ORDER BY s.id DESC LIMIT 1
"""


def fmt(t: datetime | None) -> str:
    return t.astimezone().strftime("%d.%m %H:%M") if t else "—"


def queue(conn, allowed: tuple[str, ...]) -> list[dict]:
    """Задания от давно не собиравшихся к свежим; ready — когда можно браться."""
    now = datetime.now(timezone.utc)
    rest = {mp: t + timedelta(hours=COOLDOWN.get(mp, 3)) for mp, t in conn.execute(LIMITED)}
    rows = []
    for mp, cat, last_ok, last_try in conn.execute(TARGETS):
        ready = max(last_ok + timedelta(hours=PERIOD[mp]) if last_ok else now,
                    last_try + timedelta(hours=RETRY) if last_try and last_try != last_ok else now,
                    rest.get(mp, now))
        rows.append({"mp": mp, "cat": cat, "last": last_ok, "ready": ready,
                     "manual": mp not in allowed, "now": mp in allowed and ready <= now})
    rows.sort(key=lambda r: (r["last"] is not None, r["last"] or now))
    return rows


def busy() -> bool:
    """Работает ли сборщик, запущенный вручную (свой тик — collector.schedule)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | % CommandLine"],
                         capture_output=True, text=True).stdout
    return any(re.search(r"collector\.(run|cards)\b", line) for line in out.splitlines())


def lock() -> bool:
    if LOCK.exists() and datetime.now().timestamp() - LOCK.stat().st_mtime > LOCK_STALE.total_seconds():
        LOCK.unlink()
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def step(*args: str) -> None:
    print(f"→ {' '.join(args)}", flush=True)
    code = subprocess.run([sys.executable, "-m", *args], cwd=ROOT).returncode
    if code:
        print(f"  код возврата {code}", flush=True)


def tick(mp: str, cat: str) -> None:
    print(f"сбор {mp}/{cat}, страниц {PAGES[mp]}", flush=True)
    adapter = ADAPTERS[mp]()
    try:
        with connect() as conn:
            collect(conn, adapter, cat, PAGES[mp])
            status = conn.execute(LAST_STATUS, (mp, cat)).fetchone()[0]
    finally:
        adapter.close()                  # браузерные адаптеры держат открытый Chrome
    if status == "failed":
        print("сбор не удался — обработка пропущена")
        return
    step("collector.normalize", cat)
    if mp in CARDS:
        step("collector.cards", mp, "--category", cat)
    step("collector.catalog")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Один тик планировщика сбора")
    ap.add_argument("--plan", action="store_true", help="показать очередь и выйти")
    ap.add_argument("--mp", action="append", choices=sorted(ADAPTERS),
                    help="только эти площадки; AliExpress — только так")
    args = ap.parse_args()
    allowed = tuple(args.mp or AUTO)
    print(f"=== {datetime.now():%Y-%m-%d %H:%M} планировщик", flush=True)
    with connect() as conn:
        rows = queue(conn, allowed)
    if args.plan:
        for r in rows:
            when = "вручную" if r["manual"] else "сейчас" if r["now"] else fmt(r["ready"])
            print(f"  {when:>11}  {r['mp']:4} {r['cat']:6}  последний сбор {fmt(r['last'])}")
        return
    job = next((r for r in rows if r["now"]), None)
    if job is None:
        nxt = min((r["ready"] for r in rows if not r["manual"]), default=None)
        print(f"собирать нечего; ближайшее задание — {fmt(nxt)}")
        return
    if busy():
        print("работает сборщик, запущенный вручную — тик пропущен")
        return
    if not lock():
        print("предыдущий тик ещё идёт — пропуск")
        return
    try:
        tick(job["mp"], job["cat"])
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
