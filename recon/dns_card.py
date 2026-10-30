"""Разведка: полные характеристики на карточке DNS.

Характеристики — на странице /product/characteristics/<guid>/<slug>/, но
свёрнуты: полный список подгружает кнопка «Все характеристики». Скрипт
открывает страницы видеокарты, корпуса и кулера в Chrome (CDP, как адаптер
DNS), подменяет fetch/XHR, чтобы записать запросы, жмёт кнопку, ждёт
разворота и печатает пары «название — значение» и пойманные запросы.
Запуск (Windows): .venv-win/Scripts/python.exe recon/dns_card.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.browser import Browser  # noqa: E402
from collector.db import connect  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
READY = "document.querySelector('.product-characteristics__spec') !== null"
EXPAND = r"""
(async () => {
  const log = [];
  const of = window.fetch;
  window.fetch = (u, o) => { log.push(['fetch', String(u?.url || u), o?.body ? String(o.body).slice(0, 300) : null]); return of(u, o); };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__u = m + ' ' + u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (b) { log.push(['xhr', this.__u, b ? String(b).slice(0, 300) : null]); return os.apply(this, arguments); };
  const count = () => document.querySelectorAll('.product-characteristics__spec').length;
  const before = count();
  const btn = [...document.querySelectorAll('button, a, span')].find(b => b.innerText?.trim() === 'Все характеристики');
  if (btn) btn.click();
  for (let i = 0; i < 40 && count() <= before; i++) await new Promise(r => setTimeout(r, 250));
  await new Promise(r => setTimeout(r, 500));
  const pairs = [...document.querySelectorAll('.product-characteristics__spec')].map(li => [
    li.querySelector('.product-characteristics__spec-title-content, .product-characteristics__spec-title')?.innerText.trim(),
    li.querySelector('.product-characteristics__spec-value')?.innerText.trim()]);
  const groups = [...document.querySelectorAll('.product-characteristics__group-title')].map(g => g.innerText.trim());
  return JSON.stringify({clicked: !!btn, before, after: count(), pairs, groups, log});
})()
"""

with connect() as db:
    rows = db.execute("""
        select distinct on (c.code) c.code, o.external_id, o.url from offers o
        join marketplaces mp on mp.id=o.marketplace_id join categories c on c.id=o.category_id
        where mp.code='dns' and c.code in ('gpu','case','cooler') and o.model_id is not null
        order by c.code, o.id""").fetchall()

b = Browser()
try:
    for cat, code, url in rows:
        u = url.replace("/product/", "/product/characteristics/", 1)
        ready = b.navigate(u, READY, timeout=25)
        d = json.loads(b.evaluate(EXPAND))
        (HERE / f"dns_card_{code}_full.html").write_text(
            b.evaluate("document.documentElement.outerHTML"), encoding="utf-8")
        print(f"\n== {cat} {code} ready={ready} clicked={d['clicked']} specs {d['before']} → {d['after']}")
        print(f"   groups: {d['groups']}")
        for kind, req, body in d["log"]:
            print(f"   {kind}: {req[:150]} {('BODY ' + body[:200]) if body else ''}")
        for t, v in d["pairs"]:
            print(f"     {t}: {v}"[:120])
finally:
    b.close()
