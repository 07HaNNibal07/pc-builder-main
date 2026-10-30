// ПК-Подбор: интерфейс поверх API (api/main.py). Сборка — это id деталей каталога;
// вся логика (подбор, совместимость, FPS, улучшение) — на сервере, в collector/advisor.py.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const rub = n => Math.round(n).toLocaleString('ru-RU') + ' ₽';
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const plural = (n, one, few, many) => {
  const m = Math.abs(n) % 100, d = m % 10;
  return m > 10 && m < 20 ? many : d === 1 ? one : d >= 2 && d <= 4 ? few : many;
};

async function api(path, body) {
  const r = await fetch('api/' + path, body
    ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {});
  if (!r.ok) {
    const d = await r.json().catch(() => ({}));
    throw new Error(typeof d.detail === 'string' ? d.detail : `сервер ответил ${r.status}`);
  }
  return r.json();
}
const store = {
  get(k, def) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : def; } catch { return def; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* приватный режим */ } },
};

// ---------- значки ----------
const ICONS = {
  cpu: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
  gpu: '<rect x="2" y="6" width="20" height="11" rx="2"/><circle cx="9" cy="11.5" r="3"/><circle cx="16.5" cy="11.5" r="2"/><path d="M4 17v3"/>',
  mb: '<rect x="3" y="3" width="18" height="18" rx="2"/><rect x="7" y="7" width="6" height="6" rx="1"/><path d="M16 7h2M16 10.5h2M7 16.5h10"/>',
  ram: '<rect x="2" y="7" width="20" height="9" rx="1.5"/><path d="M6 10v3M10 10v3M14 10v3M18 10v3M5 16v2.5M19 16v2.5"/>',
  ssd: '<rect x="5" y="2" width="14" height="20" rx="2"/><path d="M9 6h6M9 18h2"/>',
  cooler: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="1.8"/><path d="M12 10.2C12 7 13.5 5 16.5 5.5M13.8 12c3.2 0 5.2 1.5 4.7 4.5M12 13.8c0 3.2-1.5 5.2-4.5 4.7M10.2 12C7 12 5 10.5 5.5 7.5"/>',
  case: '<rect x="6" y="2" width="12" height="20" rx="2"/><circle cx="12" cy="15.5" r="2.5"/><path d="M9 6h6"/>',
  psu: '<rect x="2" y="5" width="20" height="14" rx="2"/><circle cx="9" cy="12" r="4"/><path d="M16 9h3M16 12h3M16 15h3"/>',
  fps: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  list: '<path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
  cube: '<path d="M12 2l9 5v10l-9 5-9-5V7z"/><path d="M12 12l9-5M12 12v10M12 12L3 7"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  up: '<path d="M12 19V5M5.5 11.5L12 5l6.5 6.5"/>',
  arrow: '<path d="M5 12h14M13 5l7 7-7 7"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5h.01"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  trend: '<path d="M3 17l5.5-5.5 4 3.5L21 6.5"/><path d="M15.5 6.5H21V12"/>',
  share: '<circle cx="18" cy="5" r="2.5"/><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="19" r="2.5"/><path d="M8.2 10.8l7.6-4.4M8.2 13.2l7.6 4.4"/>',
};
const icon = (name, cls = '') => `<svg class="ico ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;

const SHOPS = { wb: ['WB', '#a13bf7', '#fff', 'Wildberries'], ozon: ['OZON', '#005bff', '#fff', 'Ozon'],
                dns: ['DNS', '#f37a0a', '#fff', 'DNS'], ym: ['ЯМ', '#ffd21e', '#1a1a1a', 'Яндекс Маркет'],
                ali: ['Ali', '#e62e04', '#fff', 'AliExpress'] };
// В каталоге ссылки без адреса площадки (collector/catalog.py: short_url)
const SHOP_HOST = { wb: 'https://www.wildberries.ru', ozon: 'https://www.ozon.ru', dns: 'https://www.dns-shop.ru',
                    ym: 'https://market.yandex.ru', ali: 'https://aliexpress.ru' };
const shopUrl = (k, path) => !path ? null : /^https?:/.test(path) ? path : SHOP_HOST[k] + path;
// inButton — значок внутри кнопки (список замен): ссылку туда не вложить, открывает обработчик по data-href
const shop = (k, path, inButton = false) => {
  const s = SHOPS[k], url = shopUrl(k, path);
  if (!s) return '';
  const style = `style="--c:${s[1]};--t:${s[2]}"`, title = `title="Открыть в ${s[3]}"`;
  return !url ? `<i class="shop" ${style} title="${s[3]}">${s[0]}</i>`
    : inButton ? `<span class="shop" ${style} ${title} data-href="${esc(url)}">${s[0]}</span>`
    : `<a class="shop" ${style} href="${esc(url)}" target="_blank" rel="noopener" ${title}>${s[0]}</a>`;
};
const shops = (list, links = {}, inButton = false) =>
  `<span class="shops">${(list || []).map(k => shop(k, links[k], inButton)).join('')}</span>`;
const wide = matchMedia('(min-width: 1100px)');     // 3D всегда на экране, вкладки — в панели слева

const PARTS = [['cpu', 'Процессор'], ['gpu', 'Видеокарта'], ['mb', 'Материнская плата'], ['ram', 'Память'],
               ['ssd', 'SSD'], ['cooler', 'Кулер'], ['case', 'Корпус'], ['psu', 'Блок питания']];
const LABEL = Object.fromEntries(PARTS);
const idsOf = parts => Object.fromEntries(Object.entries(parts).filter(([, x]) => x).map(([k, x]) => [k, x.id]));

// ---------- состояние ----------
// drives — дополнительные накопители (SSD, HDD) сверх основного ids.ssd
const S = { ids: null, ramQty: 1, drives: [], info: null, advice: null, task: 'game', res: 1, prices: null };
const partOf = k => k.startsWith('drive:') ? S.info?.drives[+k.slice(6)] : S.info?.parts[k];
const labelOf = k => k.startsWith('drive:') ? 'Накопитель' : LABEL[k];
const driveLabel = (x, i) => `${x.form === '3.5' ? 'HDD' : 'SSD'} · накопитель ${i + 2}`;

function toast(text) {
  const t = $('#toast');
  t.textContent = text; t.hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { t.hidden = true; }, 3500);
}

// ---------- тема ----------
const theme = () => document.documentElement.dataset.theme ||
  (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
function paintTheme() {
  const dark = theme() === 'dark', b = $('#theme');
  b.innerHTML = icon(dark ? 'moon' : 'sun') + '<span class="sw"></span>';
  b.setAttribute('aria-pressed', String(dark));
  b.title = dark ? 'Светлая тема' : 'Тёмная тема';
}

// ---------- вкладки ----------
const TABS = ['podbor', 'sborka', 'fps', '3d'];
function route() {
  let t = TABS.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'podbor';
  if (t === '3d' && wide.matches) t = 'sborka';          // на широком экране 3D и так рядом
  for (const x of TABS) $('#tab-' + x).hidden = x !== t && !(x === '3d' && wide.matches);
  $$('.tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === t));
  $('.pane').scrollTop = 0;
  document.body.dataset.tab = t;
  if (t === '3d' || wide.matches) load3d();
}

// ---------- подбор ----------
const segVal = name => $(`.seg[data-name="${name}"] .on`)?.dataset.v;
const formQuery = () => ({ budget: +$('#budget').value, task: segVal('task'), res: segVal('res'),
                           cpu: $('#cpu').value || null, gpu: $('#gpu').value || null, form: $('#size').value || null });

async function runAdvice(q = formQuery()) {
  const msg = $('#advise-msg');
  msg.textContent = 'Подбираем…';
  try { S.advice = await api('advise', q); }
  catch (e) { msg.textContent = 'Не удалось подобрать: ' + e.message; return null; }
  S.task = q.task;
  if (!S.advice.variants.length) { msg.textContent = S.advice.error; $('#variants').innerHTML = ''; return S.advice; }
  msg.textContent = `Перебрано совместимых сборок: ${S.advice.searched.builds}. Наведите на деталь — почему выбрана именно она.`;
  renderVariants();
  return S.advice;
}

function variantRows(v) {
  const P = v.parts, a = S.advice;
  const rows = [['cpu', 'Процессор', esc(P.cpu.model || P.cpu.name)],
                ['gpu', 'Видеокарта', P.gpu ? esc(`${P.gpu.chip} ${P.gpu.mem || '?'} ГБ`) : 'встроенная графика'],
                ['ram', 'Память', esc(`${P.ram.cap} ГБ ${P.ram.type}`)]];
  const f = id => v.fps?.find(x => x.game === id)?.fps;
  if (v.fps) rows.push(['fps', 'FPS', `Cyberpunk 2077 — ${f('Cyberpunk 2077')} FPS<br>CS2 — ${f('Counter-Strike 2')} FPS`]);
  else if (a.task === 'work') rows.push(['fps', 'Многопоток', `${P.cpu.mt} <span class="muted">(Ryzen 5 5600 = 100)</span>`]);
  return rows.map(([k, label, val]) =>
    `<li title="${esc(v.why[k] || '')}">${icon(k)}<span>${label}</span><b>${val}</b></li>`).join('');
}

function renderVariants() {
  const a = S.advice, what = a.task === 'game' ? 'FPS' : 'производительности';
  $('#variants').innerHTML = a.variants.map((v, i) => {
    const best = v.label === 'Оптимальная', u = v.upgrade;
    return `<article class="card variant${best ? ' best' : ''}">${best ? '<span class="flag">Лучшая сборка</span>' : ''}
      <h3>${esc(v.label)}</h3><div class="price">${rub(v.total)}</div>
      ${v.compare ? `<p class="muted small">${esc(v.compare)}</p>` : ''}
      <ul class="vrows">${variantRows(v)}</ul>
      ${u ? `<button class="upgrade" type="button" data-up="${u.budget}" title="Подобрать заново с бюджетом ${rub(u.budget)}">${icon('up')}
        <span><b>Доплатить ${rub(u.delta)} → +${u.gain} % ${what}</b><small>${esc(u.changes.join(' · '))}</small></span></button>` : ''}
      <button class="btn ${best ? 'primary' : 'soft'} wide" type="button" data-pick="${i}">Выбрать эту сборку</button></article>`;
  }).join('');
}

// ---------- сборка ----------
async function setBuild(ids, ramQty = 1, from3d = false, drives = S.drives) {
  let info;
  try { info = await api('build', { ids, ram_qty: ramQty, drives }); }
  catch (e) { toast('Сборка не загрузилась: ' + e.message); return false; }
  S.ids = { ...ids }; S.ramQty = ramQty; S.drives = [...drives]; S.info = info;
  store.set('pcb-build', { ids: S.ids, ramQty, drives: S.drives });
  renderBuild(); renderFps();
  if (!from3d) send3d();
  if (location.hash === '#3d' || wide.matches) load3d();
  return true;
}

// Ссылка на сборку: ?b=p3147.g83.m1096.r3477x4.s725.k2368.c1695.w6671.h2782 — id деталей через точку
// (буква id = категория каталога), у памяти ×количество, после основного SSD — дополнительные накопители
const shareCode = () => [...PARTS.map(([k]) => S.ids[k] && S.ids[k] + (k === 'ram' && S.ramQty > 1 ? 'x' + S.ramQty : '')),
                         ...S.drives].filter(Boolean).join('.');
const CODE_PART = { p: 'cpu', g: 'gpu', m: 'mb', r: 'ram', s: 'ssd', k: 'cooler', c: 'case', w: 'psu' };
function parseShare(code) {
  const ids = {}, drives = [];
  let ramQty = 1;
  for (const t of code.split('.')) {
    const m = /^([a-z])(\d+)(?:x([1-8]))?$/.exec(t);
    if (!m) continue;
    const id = m[1] + m[2], part = CODE_PART[m[1]];
    if (part && !ids[part]) { ids[part] = id; if (part === 'ram' && m[3]) ramQty = +m[3]; }
    else if (m[1] === 's' || m[1] === 'h') drives.push(id);
  }
  return ids.cpu ? { ids, ramQty, drives } : null;
}
async function shareBuild() {
  const url = `${location.origin}${location.pathname}?b=${shareCode()}#sborka`;
  try { await navigator.clipboard.writeText(url); toast('Ссылка на сборку скопирована'); }
  catch { prompt('Скопируйте ссылку на сборку:', url); }
}

// Период сбора цен: «12 сент.», «10–12 сент.», «28 авг. — 12 сент.»
function pricesLabel({ from, to }) {
  const d = s => new Date(s + 'T00:00'), day = s => d(s).getDate(), full = s => d(s).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
  if (from === to) return full(to);
  return d(from).getMonth() === d(to).getMonth() ? `${day(from)}–${full(to)}` : `${full(from)} — ${full(to)}`;
}

function renderBuild() {
  const I = S.info, bad = new Set(I.checks.filter(c => !c.ok).map(c => c.part));
  $('#top-total').textContent = rub(I.total);
  $('#build-total').textContent = rub(I.total);
  const count = PARTS.filter(([k]) => I.parts[k]).length + I.drives.length;
  $('#build-sub').textContent = `${count} ${plural(count, 'комплектующая', 'комплектующие', 'комплектующих')} · цены с 5 маркетплейсов` +
    (S.prices ? ` · цены собраны ${pricesLabel(S.prices)}` : '');
  // Строка детали: что это, модель, где купить (значки площадок — ссылки), цена — ссылка на самое дешёвое
  const row = (k, label, x, { qty = '', ico = k, isBad = bad.has(k), remove = null } = {}) => {
    const price = x ? (x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener" title="Открыть самое дешёвое предложение">${rub(x.price)}</a>` : rub(x.price)) + qty : '—';
    const hist = x ? `<button class="hist-btn" type="button" data-hist="${k}" title="История цен" aria-label="История цен">${icon('trend')}</button>` : '';
    const drop = remove === null ? '' : `<button class="btn ghost sm" type="button" data-remove="${remove}">Убрать</button>`;
    return `<li class="prow${isBad ? ' bad' : ''}${S.focus === k ? ' on' : ''}" data-part="${k}"><span class="ic">${icon(ico)}</span>
      <div class="pmain"><small>${label}</small><b title="${esc(x?.name)}">${x ? esc(x.name) : '<span class="muted">встроенная графика</span>'}</b>
        ${x ? shops(x.shops, x.links) : ''}</div>
      <div class="pside"><span class="pprice num">${price}${hist}</span>
        <span class="pbtns"><button class="btn ghost sm" type="button" data-replace="${k}">${x ? 'Заменить' : 'Добавить'}</button>${drop}</span></div></li>`;
  };
  // Дополнительные накопители — сразу под основным SSD; «добавить» — под ними, только если есть свободное место
  const drives = () => I.drives.map((x, i) => row(`drive:${i}`, driveLabel(x, i), x, { ico: 'ssd', isBad: bad.has('ssd'), remove: i })).join('') +
    (I.drive_room ? `<li class="padd"><button class="btn ghost sm" type="button" data-replace="drive:new">+ Добавить накопитель</button></li>` : '');
  $('#parts').innerHTML = PARTS.map(([k, label]) => row(k, label, I.parts[k], { qty: k === 'ram' && S.ramQty > 1 ? ` × ${S.ramQty}` : '' }) +
    (k === 'ssd' ? drives() : '')).join('');
  const n = bad.size ? I.checks.filter(c => !c.ok).length : 0;
  $('#compat-status').className = 'status ' + (n ? 'bad' : 'ok');
  $('#compat-status').textContent = n ? `${n} ${plural(n, 'конфликт', 'конфликта', 'конфликтов')}` : 'Всё совместимо';
  $('#checks').innerHTML = [...I.checks].sort((a, b) => a.ok - b.ok).map(c =>
    `<li class="${c.ok ? 'ok' : 'bad'}">${icon(c.ok ? 'check' : 'x', 'dot')}<div><b>${esc(c.title)}</b><small>${esc(c.detail)}</small></div></li>`).join('');
}

// ---------- история цен ----------
// history.json собирает collector/catalog.py: {days, models: {id: {площадка: [[день, цена], …]}}},
// точки — только изменения цены и последний снимок; между ними цена та же
let HIST = null;
const loadHist = async () => HIST ??= await fetch('app/history.json', { cache: 'no-cache' })
  .then(r => r.ok ? r.json() : null).catch(() => null) || { days: [], models: {} };
const dayLabel = d => new Date(d + 'T00:00').toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
// Жёлтый ЯМ на светлом фоне теряется — линия на ступень темнее; подписи — всегда цветом текста
const shopColor = k => k === 'ym' && theme() !== 'dark' ? '#c29400' : SHOPS[k][1];
const niceStep = span => { const p = 10 ** Math.floor(Math.log10(span || 1)); return [1, 2, 2.5, 5, 10].map(k => k * p).find(s => s >= span); };

async function openHist(part) {
  const x = partOf(part);
  if (!x) return;
  $('#hist-title').textContent = x.name;
  $('#hist-sub').textContent = `${labelOf(part)} · сейчас от ${rub(x.price)}`;
  $('#hist-chart').innerHTML = ''; $('#hist-note').textContent = 'Загружаем историю…';
  $('#hist').showModal();
  const h = await loadHist();
  renderHist(h.days, h.models[x.id] || {});
}

function renderHist(days, m) {
  const box = $('#hist-chart'), note = $('#hist-note'), table = $('#hist-table');
  const keys = Object.keys(SHOPS).filter(k => m[k]?.length), pts = keys.flatMap(k => m[k]);
  const idx = [...new Set(pts.map(p => p[0]))].sort((a, b) => a - b);
  $('#hist-legend').innerHTML = keys.map(k =>
    `<span><i class="lk" style="--c:${shopColor(k)}"></i>${SHOPS[k][3]} <b>${rub(m[k].at(-1)[1])}</b></span>`).join('');
  if (!pts.length) {
    box.innerHTML = ''; table.innerHTML = '';
    note.textContent = 'В истории нет цен «в наличии» для этой модели.';
    return;
  }
  const i0 = idx[0], i1 = idx.at(-1);
  note.textContent = i0 === i1
    ? `История только копится: есть снимок цен от ${dayLabel(days[i0])}. Линии появятся после следующего сбора.`
    : `Минимальная цена «в наличии» на каждой площадке по дням сбора (${dayLabel(days[i0])} — ${dayLabel(days[i1])}). Между сборами цена считается неизменной.`;
  // цена площадки на день i: последняя известная, в пределах её наблюдений
  const at = (k, i) => {
    const s = m[k];
    if (i < s[0][0] || i > s.at(-1)[0]) return null;
    let p = null;
    for (const q of s) if (q[0] <= i) p = q[1];
    return p;
  };

  const W = 640, H = 220, L = 74, R = 18, T = 12, B = 26;
  let lo = Math.min(...pts.map(p => p[1])), hi = Math.max(...pts.map(p => p[1]));
  if (hi - lo < lo * 0.04) { lo *= 0.97; hi *= 1.03; }
  const step = niceStep((hi - lo) / 4), y0 = Math.floor(lo / step) * step, y1 = Math.ceil(hi / step) * step;
  const X = i => i1 === i0 ? (L + W - R) / 2 : L + (i - i0) / (i1 - i0) * (W - L - R);
  const Y = p => T + (y1 - p) / (y1 - y0) * (H - T - B);
  const ticks = [];
  for (let v = y0; v <= y1 + step / 2; v += step) ticks.push(v);
  const xl = idx.length <= 7 ? idx : [i0, idx[idx.length >> 1], i1];
  const f = n => n.toFixed(1);
  const line = k => m[k].map(([i, p], j) => j ? `H${f(X(i))}V${f(Y(p))}` : `M${f(X(i))},${f(Y(p))}`).join('');
  box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" tabindex="0" role="img" aria-label="Цена по дням; стрелки — выбрать день">
    ${ticks.map(v => `<line class="grid" x1="${L}" x2="${W - R}" y1="${f(Y(v))}" y2="${f(Y(v))}"/>
      <text class="tick" x="${L - 8}" y="${f(Y(v) + 4)}" text-anchor="end">${rub(v)}</text>`).join('')}
    ${xl.map(i => `<text class="tick" x="${f(X(i))}" y="${H - 6}" text-anchor="middle">${dayLabel(days[i])}</text>`).join('')}
    ${keys.map(k => `<path class="ln" d="${line(k)}" stroke="${shopColor(k)}"/>`).join('')}
    <line class="xh" y1="${T}" y2="${H - B}" opacity="0"/>
    ${keys.map(k => { const [i, p] = m[k].at(-1); return `<circle class="dot" cx="${f(X(i))}" cy="${f(Y(p))}" r="4" fill="${shopColor(k)}"/>`; }).join('')}
    <rect x="${L - 12}" y="0" width="${W - L - R + 24}" height="${H}" fill="transparent"/>
  </svg><div class="hist-tip" hidden></div>`;

  const svg = $('svg', box), tip = $('.hist-tip', box), xh = $('.xh', svg);
  let cur = i1;
  const show = i => {
    cur = i;
    xh.setAttribute('x1', X(i)); xh.setAttribute('x2', X(i)); xh.setAttribute('opacity', '1');
    const rows = keys.map(k => [k, at(k, i)]).filter(r => r[1] != null).sort((a, b) => a[1] - b[1]);
    tip.innerHTML = `<small class="muted">${dayLabel(days[i])}</small>` + rows.map(([k, p]) =>
      `<div><i class="lk" style="--c:${shopColor(k)}"></i><b>${rub(p)}</b><span class="muted">${SHOPS[k][3]}</span></div>`).join('');
    tip.hidden = false;
    const x = X(i) * svg.clientWidth / W;
    tip.style.left = (x + tip.offsetWidth + 16 > svg.clientWidth ? x - tip.offsetWidth - 12 : x + 12) + 'px';
  };
  const hide = () => { tip.hidden = true; xh.setAttribute('opacity', '0'); };
  const near = px => idx.reduce((a, b) => Math.abs(X(b) - px) < Math.abs(X(a) - px) ? b : a);
  svg.onpointermove = e => { const r = svg.getBoundingClientRect(); show(near((e.clientX - r.left) * W / r.width)); };
  svg.onpointerleave = hide;
  svg.onfocus = () => show(cur);
  svg.onblur = hide;
  svg.onkeydown = e => {
    const j = idx.indexOf(cur) + (e.key === 'ArrowRight') - (e.key === 'ArrowLeft');
    if (idx[j] != null && idx[j] !== cur) { e.preventDefault(); show(idx[j]); }
  };

  table.innerHTML = `<thead><tr><th>День</th>${keys.map(k => `<th>${SHOPS[k][3]}</th>`).join('')}</tr></thead><tbody>` +
    idx.map(i => `<tr><td>${dayLabel(days[i])}</td>${keys.map(k => { const p = at(k, i); return `<td>${p == null ? '—' : rub(p)}</td>`; }).join('')}</tr>`).join('') +
    '</tbody>';
}

// ---------- FPS ----------
// Капсулы Steam (web/app/games); у Valorant и Fortnite страниц в Steam нет — буква
const GAME_IMG = new Set(['cs2', 'dota2', 'deadlock', 'gta5', 'tarkov', 'rdr2', 'bg3', 'cyberpunk', 'stalker2', 'wukong']);
const fpsClass = f => f >= 60 ? 'good' : f >= 40 ? 'mid' : 'low';
function renderFps() {
  const rows = S.info?.fps || [], r = S.res, RES = S.info?.res || ['1080p', '1440p', '4K'];
  $('#fps-summary').hidden = !rows.length;
  if (!rows.length) {
    $('#fps-table').innerHTML = '<tbody><tr><td class="muted">Без видеокарты FPS в играх не оцениваем — добавьте её во вкладке «Сборка».</td></tr></tbody>';
    return;
  }
  $('#fps-table').innerHTML = `<thead><tr><th>Игра</th>${RES.map((x, i) => `<th class="r${i === r ? ' cur' : ''}">${x}</th>`).join('')}<th class="r">Узкое место</th></tr></thead><tbody>` +
    rows.map((g, gi) => `<tr><td><span class="gname">${GAME_IMG.has(g.id)
      ? `<img class="gimg" src="app/games/${g.id}.jpg" alt="" loading="lazy">`
      : `<span class="gbadge" style="--h:${(gi * 47 + 190) % 360}">${esc(g.game[0])}</span>`}${esc(g.game)}</span></td>` +
      g.fps.map((f, i) => `<td class="r num ${fpsClass(f)}${i === r ? ' cur' : ''}"${g.vram[i] ? ' title="Не хватает видеопамяти"' : ''}>${f}${g.vram[i] ? '<sup>VRAM</sup>' : ''}</td>`).join('') +
      `<td class="r"><span class="lim ${g.limit[r]}" title="${g.limit[r] === 'cpu' ? 'Упор в процессор' : 'Упор в видеокарту'}">${g.limit[r].toUpperCase()}</span></td></tr>`).join('') + '</tbody>';
  const at = rows.map(g => g.fps[r]), max = Math.max(...at);
  $('#fps-avg').textContent = Math.round(at.reduce((a, b) => a + b, 0) / at.length) + ' FPS';
  $('#fps-avg-res').textContent = `в среднем по ${at.length} играм в ${RES[r]}`;
  $('#fps-bars').innerHTML = [...at].sort((a, b) => a - b).map(f =>
    `<i class="${fpsClass(f)}" style="height:${Math.max(6, f / max * 100)}%" title="${f} FPS"></i>`).join('');
  const good = at.filter(f => f >= 60).length, mid = at.filter(f => f >= 40 && f < 60).length, low = at.length - good - mid;
  const cpu = rows.filter(g => g.limit[r] === 'cpu').length;
  $('#fps-stats').innerHTML = [
    ['good', good, `${plural(good, 'игра идёт', 'игры идут', 'игр идут')} от 60 FPS`],
    ['mid', mid, 'потребуют снизить настройки (40–60)'],
    ['low', low, 'ниже 40 FPS — лучше снизить разрешение'],
    ['', cpu, `с упором в процессор — ${cpu > at.length / 2 ? 'стоит усилить процессор' : 'видеокарта раскрыта'}`],
  ].map(([c, n, t]) => `<li class="${c}"><b>${n}</b>${t}</li>`).join('');
  $$('.seg[data-name="fpsres"] button').forEach(b => b.classList.toggle('on', +b.dataset.v === r));
}

// ---------- улучшить сборку ----------
async function runUpgrade() {
  const out = $('#up-out'), money = +$('#up-money').value;
  if (!S.ids) return;
  out.innerHTML = '<p class="muted small">Считаем варианты…</p>';
  let r;
  try {
    r = await api('upgrade', { ids: S.ids, drives: S.drives, ram_qty: S.ramQty, money, task: S.task, res: S.info.res[S.res],
                              resale: $('#up-resale').checked });
  }
  catch (e) { out.innerHTML = `<p class="muted small">Не удалось: ${esc(e.message)}</p>`; return; }
  // Сначала — прирост FPS/производительности, ниже — улучшения, которых модель FPS не видит
  const extras = r.extras || [];
  S.upgrade = { ...r, all: [...r.suggestions, ...extras] };
  if (!S.upgrade.all.length) { out.innerHTML = `<p class="muted small">${esc(r.error || 'Улучшений не нашлось')}</p>`; return; }
  const what = S.task === 'game' ? 'FPS' : 'производительности';
  const resaleLine = s => s.resale ? `<small>новые детали ${rub(s.gross)} − продажа старых ≈ ${rub(s.resale)}</small>` : '';
  const apply = i => `<button class="btn soft sm" type="button" data-apply="${i}">Применить</button>`;
  const QKIND = { ssd: 'SSD', ram: 'Память', ddr5: 'DDR5', cooler: 'Кулер', case: 'Корпус', psu: 'Блок питания' };
  out.innerHTML = (r.suggestions.length ? r.suggestions.map((s, i) => `<div class="sugg">${s.tag ? `<span class="tag">${esc(s.tag)}</span>` : ''}
      <b>${esc(s.title)}</b><div class="gain">+${s.gain} % ${what}<span>за ${rub(s.cost)}</span></div>
      ${resaleLine(s)}${s.fps ? `<small>${esc(s.fps)}</small>` : ''}<small>${esc(s.changes.join(' · '))}</small>${apply(i)}</div>`).join('')
      : `<p class="muted small">${esc(r.error)}</p>`) +
    (extras.length ? `<h3 class="up-sub">Не про FPS, но полезно</h3>` + extras.map((s, j) => `<div class="sugg q">
      <span class="tag">${esc(QKIND[s.kind] || s.kind)}</span><b>${esc(s.title)}</b><p class="benefit">${esc(s.benefit)}</p>
      <div class="gain q">за ${rub(s.cost)}</div>${resaleLine(s)}<small>${esc(s.changes.join(' · '))}</small>${apply(r.suggestions.length + j)}</div>`).join('') : '') +
    `<p class="note">${icon('info')}<span>${$('#up-resale').checked ? 'Цена — новые детали минус продажа старых.' : 'Цена — только новых деталей.'} Остальное остаётся, совместимость проверена.</span></p>`;
}

// ---------- выбор детали ----------
let pickPart = null, pickTimer = 0;
// Фильтры окна замены: {ключ: [значения]} для галочек, {ключ: [от, до]} для диапазонов; fitOnly — только подходящие.
// Какие фильтры есть у детали и их значения со счётчиками — присылает сервер (advisor.FACETS)
const PF = { flt: {}, fitOnly: false, more: new Set(), facets: [] };
function openPicker(part) {
  if (part.replace(/:.*/, '') !== pickPart?.replace(/:.*/, '')) { PF.flt = {}; PF.more.clear(); }
  PF.fitOnly = false;
  $('#picker').classList.remove('pf-open');
  pickPart = part;
  $('#picker-title').textContent = part === 'drive:new' ? 'Добавить накопитель' : 'Заменить: ' + labelOf(part).toLowerCase();
  $('#picker-q').value = '';
  $('#picker-list').innerHTML = '';
  $('#picker').showModal();
  loadOptions();
}
async function loadOptions() {
  const list = $('#picker-list'), part = pickPart;
  let r;
  // дополнительный накопитель: SSD и HDD одним списком; drive — какой заменить, без него — добавить
  const which = part.startsWith('drive:') ? { part: 'drive', drive: part === 'drive:new' ? null : +part.slice(6) } : { part };
  const sort = segVal('psort') || 'fit';
  const seq = loadOptions.seq = (loadOptions.seq || 0) + 1;
  try { r = await api('options', { ids: S.ids, drives: S.drives, ram_qty: S.ramQty, ...which, q: $('#picker-q').value.trim(), sort, limit: 80,
                                   filters: PF.flt, fit_only: PF.fitOnly }); }
  catch (e) { list.innerHTML = `<li class="muted">${esc(e.message)}</li>`; return; }
  if (seq !== loadOptions.seq) return;       // пока ждали ответа, сменили деталь, сортировку или фильтры
  renderFilters(r.facets);
  const order = { fit: 'Сверху — текущая и подходящие, по цене.', asc: 'Сначала дешёвые, неподходящие — в конце.',
                  desc: 'Сначала дорогие, неподходящие — в конце.' }[sort];
  $('#picker-count').textContent = `Найдено: ${r.found}${r.found > r.items.length ? `, показаны первые ${r.items.length}` : ''}. ${order}`;
  list.scrollTop = 0;
  list.innerHTML = r.items.map(o => `<li><button class="opt${o.current ? ' cur' : ''}${o.problem ? ' bad' : ''}" type="button" data-id="${o.id}">
      <span class="o-main"><b title="${esc(o.name)}">${esc(o.name)}</b><small>${esc(o.spec)}</small>${o.problem ? `<small class="warn${o.soft ? ' soft' : ''}"${o.soft ? ' title="Подойдёт, если заменить и другую деталь"' : ''}>${esc(o.problem)}</small>` : ''}</span>
      <span class="o-side"><b>${rub(o.price)}</b>${shops(o.shops, o.links, true)}${o.current ? '<small>сейчас в сборке</small>' : ''}</span></button></li>`).join('') ||
    '<li class="muted small">Ничего не нашлось</li>';
}

const pfCount = () => Object.values(PF.flt).filter(v => v?.some(x => x !== null && x !== '')).length + PF.fitOnly;
function renderFilters(facets = PF.facets) {
  PF.facets = facets;
  const box = $('#pfilters'), top = box.scrollTop, n = pfCount();
  $('#pf-toggle').textContent = n ? `Фильтры · ${n}` : 'Фильтры';
  const group = f => {
    if (f.kind === 'range') {
      const [lo, hi] = PF.flt[f.key] || [];
      const inp = (i, v, ph) => `<input type="number" inputmode="numeric" data-range="${f.key}" data-i="${i}" value="${v ?? ''}" placeholder="${ph}" aria-label="${esc(f.label)} ${i ? 'до' : 'от'}">`;
      return `<fieldset class="pf-group"><legend>${esc(f.label)}</legend><div class="pf-range">${inp(0, lo, 'от ' + f.min)}<span class="muted">—</span>${inp(1, hi, 'до ' + f.max)}</div></fieldset>`;
    }
    const on = new Set(PF.flt[f.key] || []), all = PF.more.has(f.key), LIMIT = 7;
    // свёрнутый список: самые частые LIMIT и все отмеченные — в исходном порядке (числа — по возрастанию)
    const top = new Set([...f.values].sort((a, b) => b.n - a.n).slice(0, LIMIT).map(v => v.v));
    const shown = all || f.values.length <= LIMIT + 1 ? f.values : f.values.filter(v => top.has(v.v) || on.has(v.v));
    const label = v => f.key === 'shop' && SHOPS[v.v] ? SHOPS[v.v][3] : v.label;
    return `<fieldset class="pf-group"><legend>${esc(f.label)}</legend>` + shown.map(v =>
      `<label class="pf-opt${!v.n && !on.has(v.v) ? ' off' : ''}"><input type="checkbox" data-set="${f.key}" value="${esc(v.v)}"${on.has(v.v) ? ' checked' : ''}${!v.n && !on.has(v.v) ? ' disabled' : ''}>
        <span title="${esc(label(v))}">${esc(label(v))}</span><small>${v.n}</small></label>`).join('') +
      (shown.length < f.values.length || all && f.values.length > LIMIT + 1
        ? `<button class="pf-more" type="button" data-more="${f.key}">${all ? 'Свернуть' : `Показать все (${f.values.length})`}</button>` : '') + '</fieldset>';
  };
  box.innerHTML = `<div class="pf-head"><b>Фильтры</b>${n ? '<button class="pf-reset" type="button" data-reset>Сбросить</button>' : ''}</div>
    <label class="pf-opt fit"><input type="checkbox" data-fit${PF.fitOnly ? ' checked' : ''}><span>Только подходящие</span></label>` +
    facets.map(group).join('');
  box.scrollTop = top;
}
function onFilter(e) {
  const t = e.target;
  if (t.dataset.fit !== undefined) PF.fitOnly = t.checked;
  else if (t.dataset.set) {
    const cur = new Set(PF.flt[t.dataset.set] || []);
    t.checked ? cur.add(t.value) : cur.delete(t.value);
    PF.flt[t.dataset.set] = [...cur];
  } else if (t.dataset.range) {
    const r = PF.flt[t.dataset.range] || [null, null], v = t.value.trim();
    r[+t.dataset.i] = v === '' ? null : +v;
    PF.flt[t.dataset.range] = r;
  } else return;
  loadOptions();
}

// ---------- 3D: прототип во фрейме, сборка синхронизируется в обе стороны ----------
let frameLoaded = false;
function load3d() {
  if (frameLoaded || !S.ids) return;
  // v — чтобы фрейм не взял 3D-страницу из кеша: Ctrl+F5 перезагружает только саму страницу
  const p = new URLSearchParams({ embed: '1', v: Date.now().toString(36), theme: theme(), ...S.ids });
  if (S.ramQty > 1) p.set('ramqty', S.ramQty);
  if (S.drives.length) p.set('drives', [S.ids.ssd, ...S.drives].join(','));    // модель: все накопители списком
  $('#frame').src = 'prototype-3d/index.html?' + p;
  frameLoaded = true;
}
function send3dTheme() {
  if (frameLoaded) $('#frame').contentWindow?.postMessage({ type: 'pcb-theme', theme: theme() }, '*');
}
function send3d() {
  if (frameLoaded) $('#frame').contentWindow?.postMessage({ type: 'pcb-set', ids: S.ids, ramQty: S.ramQty, drives: S.drives }, '*');
}
// Выбранная деталь: клик по строке «Сборки» открывает её карточку в 3D, выбор в 3D подсвечивает строку
function markFocus(k) {
  S.focus = k;
  $$('#parts .prow').forEach(li => li.classList.toggle('on', li.dataset.part === k));
}
function focus3d(k) {
  markFocus(k);
  const d = k.startsWith('drive:');       // в модели накопители списком: 0 — основной, дальше — дополнительные
  if (frameLoaded) $('#frame').contentWindow?.postMessage({ type: 'pcb-focus', part: d ? 'ssd' : k, drive: d ? +k.slice(6) + 1 : 0 }, '*');
}
window.addEventListener('message', e => {
  if (e.source !== $('#frame').contentWindow || !S.ids) return;
  if (e.data?.type === 'pcb-focus') { markFocus(e.data.part === 'ssd' && e.data.drive > 0 ? `drive:${e.data.drive - 1}` : e.data.part); return; }
  if (e.data?.type !== 'pcb-sel') return;
  const ids = Object.fromEntries(Object.entries(e.data.ids).filter(([, v]) => v));
  if (!S.ids.gpu) delete ids.gpu;           // модели нужна видеокарта, сборке без неё — нет
  const drives = e.data.drives || [];
  const same = PARTS.every(([k]) => (ids[k] || null) === (S.ids[k] || null)) && e.data.ramQty === S.ramQty &&
    drives.join() === S.drives.join();
  if (!same) setBuild(ids, e.data.ramQty || 1, true, drives);
});

// ---------- запуск ----------
async function init() {
  for (const el of $$('[data-icon]')) el.outerHTML = icon(el.dataset.icon);
  paintTheme();
  $('#theme').onclick = () => {
    const t = theme() === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem('pcb-theme', t); } catch { /* приватный режим */ }
    paintTheme(); send3dTheme();
  };
  // тема «как в системе» — следить и за сменой системной
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!document.documentElement.dataset.theme) { paintTheme(); send3dTheme(); }
  });
  $('#shops-legend').innerHTML = Object.keys(SHOPS).map(k => `<span>${shop(k)}${SHOPS[k][3]}</span>`).join('');

  // «−»/«+» у полей с суммой: шаг — из step поля, в пределах min/max
  for (const box of $$('.money')) {
    const inp = $('input', box), cur = box.querySelector('i');
    for (const [d, t, label] of [[-1, '−', 'Меньше'], [1, '+', 'Больше']]) {
      const btn = document.createElement('button');
      Object.assign(btn, { type: 'button', className: 'step', textContent: t });
      btn.setAttribute('aria-label', label);
      btn.onclick = () => { d < 0 ? inp.stepDown() : inp.stepUp(); inp.dispatchEvent(new Event('input', { bubbles: true })); };
      box.insertBefore(btn, cur);
    }
  }
  for (const seg of $$('.seg')) seg.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    $$('button', seg).forEach(x => x.classList.toggle('on', x === b));
    if (seg.dataset.name === 'task') $('#res-field').classList.toggle('off', b.dataset.v !== 'game');
    if (seg.dataset.name === 'fpsres') { S.res = +b.dataset.v; renderFps(); }
    if (seg.dataset.name === 'psort') loadOptions();
  });
  $('#form').onsubmit = e => { e.preventDefault(); runAdvice(); };
  $('#variants').onclick = async e => {
    const b = e.target.closest('button');
    if (b?.dataset.up) { $('#budget').value = b.dataset.up; runAdvice(); }
    else if (b?.dataset.pick) {
      const v = S.advice.variants[+b.dataset.pick];
      S.res = Math.max(0, ['1080p', '1440p', '4K'].indexOf(S.advice.res));
      if (await setBuild(idsOf(v.parts), 1, false, [])) { location.hash = 'sborka'; toast(`Сборка «${v.label}» выбрана`); }
    }
  };
  $('#parts').onclick = async e => {
    const b = e.target.closest('[data-replace], [data-hist], [data-remove]');
    if (!b) {                                 // клик по строке мимо кнопок и ссылок — показать деталь в 3D
      const li = !e.target.closest('a, button') && e.target.closest('.prow[data-part]');
      if (li) focus3d(li.dataset.part);
      return;
    }
    if (b.dataset.replace) openPicker(b.dataset.replace);
    else if (b?.dataset.hist) openHist(b.dataset.hist);
    else if (b?.dataset.remove && await setBuild(S.ids, S.ramQty, false, S.drives.filter((_, i) => i !== +b.dataset.remove)))
      toast('Накопитель убран');
  };
  $('#pfilters').addEventListener('change', onFilter);
  $('#pfilters').onclick = e => {
    const b = e.target.closest('[data-more], [data-reset]');
    if (!b) return;
    if (b.dataset.more) { PF.more.has(b.dataset.more) ? PF.more.delete(b.dataset.more) : PF.more.add(b.dataset.more); renderFilters(); }
    else { PF.flt = {}; PF.fitOnly = false; loadOptions(); }
  };
  $('#pf-toggle').onclick = () => {
    const open = $('#picker').classList.toggle('pf-open');
    $('#pf-toggle').setAttribute('aria-expanded', String(open));
  };
  $('#picker-q').oninput = () => { clearTimeout(pickTimer); pickTimer = setTimeout(loadOptions, 250); };
  $('#picker-list').onclick = async e => {
    const link = e.target.closest('[data-href]');
    if (link) { open(link.dataset.href, '_blank', 'noopener'); return; }    // значок магазина — не выбор детали
    const b = e.target.closest('[data-id]');
    if (!b) return;
    $('#picker').close();
    if (pickPart.startsWith('drive:')) {
      const d = [...S.drives], i = pickPart === 'drive:new' ? d.length : +pickPart.slice(6);
      if (d[i] === b.dataset.id) return;
      d[i] = b.dataset.id;
      if (await setBuild(S.ids, S.ramQty, false, d)) toast(pickPart === 'drive:new' ? 'Накопитель добавлен' : 'Накопитель заменён');
      return;
    }
    if (b.dataset.id !== S.ids[pickPart] && await setBuild({ ...S.ids, [pickPart]: b.dataset.id }, S.ramQty))
      toast(`${LABEL[pickPart]} ${['gpu', 'mb', 'ram'].includes(pickPart) ? 'заменена' : 'заменён'}`);
  };
  $('#share').onclick = shareBuild;
  $('#up-form').onsubmit = e => { e.preventDefault(); runUpgrade(); };
  $('#up-out').onclick = async e => {
    const b = e.target.closest('[data-apply]');
    if (b && await setBuild(S.upgrade.all[+b.dataset.apply].ids, 1)) {
      toast('Улучшение применено'); $('#up-out').innerHTML = '';
    }
  };

  route();
  addEventListener('hashchange', route);
  wide.addEventListener('change', route);
  if (!location.protocol.startsWith('http')) {
    $('#advise-msg').textContent = 'Интерфейс работает через сервер: docker compose up -d api, затем http://localhost:8792';
    return;
  }
  api('health').then(h => { S.prices = h.prices; if (S.info) renderBuild(); }).catch(() => {});
  // Открыли по ссылке «Поделиться» — показать ту сборку; параметр убрать, чтобы обновление страницы не затирало свою
  const code = new URLSearchParams(location.search).get('b'), shared = code && parseShare(code);
  if (code) history.replaceState(null, '', location.pathname + (shared ? '#sborka' : location.hash));
  const saved = store.get('pcb-build', null);
  const adv = await runAdvice();                  // стартовый подбор — сразу видно, что умеет система
  if (shared) {
    if (await setBuild(shared.ids, shared.ramQty, false, shared.drives)) { route(); toast('Открыта сборка по ссылке'); return; }
    toast('Сборка по ссылке не найдена — детали могли пропасть из каталога');
  }
  if (saved?.ids && await setBuild(saved.ids, saved.ramQty || 1, false, saved.drives || [])) return;
  const v = adv?.variants?.find(x => x.label === 'Оптимальная');
  if (v) setBuild(idsOf(v.parts), 1, false, []);
}
init();
