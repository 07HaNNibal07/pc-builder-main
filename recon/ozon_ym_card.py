"""Разведка: где на карточках Ozon и Яндекс Маркета лежат характеристики.

Ozon: страница товара собирается из entrypoint-api; характеристики — в виджете
webCharacteristics, обычно на второй «странице» раскладки (layout_page_index=2).
Запрос выполняется fetch'ем из вкладки ozon.ru, как в адаптере Ozon.
ЯМ: открывается страница характеристик карточки (<url>/spec), HTML сохраняется
в recon/ym_spec_<id>.html, печатаются строки с размерами и значения data-auto.
Запуск (Windows): .venv-win/Scripts/python.exe recon/ozon_ym_card.py
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.browser import Browser  # noqa: E402
from collector.db import connect  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).parent
KW = re.compile(r"длин|высот|толщин|ширин|слот|видеокарт|кулер|форм-фактор|частот|m\.2", re.I)

OZON_FETCH = r"""
(async () => {
  const out = {};
  for (const idx of [1, 2]) {
    const page = %s + '?layout_container=pdpPage2column&layout_page_index=' + idx;
    const r = await fetch('/api/entrypoint-api.bx/page/json/v2?url=' + encodeURIComponent(page));
    const res = {status: r.status, keys: [], pairs: []};
    if (r.ok) {
      const j = await r.json();
      const ws = j.widgetStates || {};
      res.keys = Object.keys(ws).map(k => k.split('-')[0]);
      for (const [k, v] of Object.entries(ws)) {
        if (!/Characteristics/i.test(k)) continue;
        const s = JSON.parse(v);
        for (const block of s.characteristics || [])
          for (const it of [...(block.short || []), ...(block.long || [])])
            res.pairs.push([it.name, (it.values || []).map(x => x.text).join(', ')]);
      }
    }
    out[idx] = res;
  }
  return JSON.stringify(out);
})()
"""

YM_EXTRACT = r"""
(() => {
  const autos = new Set();
  document.querySelectorAll('[data-auto]').forEach(e => autos.add(e.dataset.auto));
  const rows = [...document.querySelectorAll('dl, tr, li, div')].filter(e => e.children.length <= 4)
    .map(e => e.innerText?.replace(/\s+/g, ' ').trim()).filter(t => t && t.length < 120);
  return JSON.stringify({title: document.title, url: location.href, autos: [...autos].filter(a => /spec|charact|prop/i.test(a)), rows});
})()
"""

with connect() as db:
    rows = db.execute("""
        select distinct on (mp.code, c.code) mp.code, c.code, o.external_id, o.url from offers o
        join marketplaces mp on mp.id=o.marketplace_id join categories c on c.id=o.category_id
        where mp.code in ('ozon','ym') and c.code in ('gpu','case','cooler') and o.model_id is not null
        order by mp.code, c.code, o.id""").fetchall()

b = Browser()
try:
    b.navigate("https://www.ozon.ru/", "true", timeout=30)
    for mp, cat, ext, url in [r for r in rows if r[0] == "ozon"]:
        path = re.sub(r"^https://www\.ozon\.ru", "", url).split("?")[0]
        d = json.loads(b.evaluate(OZON_FETCH % json.dumps(path)))
        print(f"\n== ozon {cat} {ext}")
        for idx, res in d.items():
            print(f"   page {idx}: HTTP {res['status']}, пар {len(res['pairs'])}, виджеты: {sorted(set(res['keys']))[:25]}")
            for t, v in res["pairs"]:
                if KW.search(t):
                    print(f"     {t}: {v}"[:120])
    for mp, cat, ext, url in [r for r in rows if r[0] == "ym"]:
        ready = b.navigate(url.rstrip("/") + "/spec", "document.body.innerText.length > 2000", timeout=30)
        d = json.loads(b.evaluate(YM_EXTRACT))
        (HERE / f"ym_spec_{ext}.html").write_text(b.evaluate("document.documentElement.outerHTML"), encoding="utf-8")
        print(f"\n== ym {cat} {ext} ready={ready} {d['url'][:90]}\n   title: {d['title'][:80]}\n   data-auto: {d['autos'][:20]}")
        seen = set()
        for t in d["rows"]:
            if KW.search(t) and t not in seen:
                seen.add(t)
                print(f"     {t}"[:120])
finally:
    b.close()
