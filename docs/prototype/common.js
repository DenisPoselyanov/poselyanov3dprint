// Спільне: іконки, дані, утиліти, перемикачі оболонки
const PATHS = {
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
  flame: '<path d="M12 22c4 0 7-3 7-7 0-4-3-6-4-9-1 2-2 3-4 3 0-2 0-4-1-6-3 3-5 7-5 12 0 4 3 7 7 7Z"/>',
  heart: '<path d="M19 14c1.5-1.5 3-3.2 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.8 0-3 .5-4.5 2-1.5-1.5-2.7-2-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4 3 5.5l7 7Z"/>',
  package: '<path d="M21 8 12 3 3 8v8l9 5 9-5Z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>',
  cart: '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.7 12.4a2 2 0 0 0 2 1.6h7.6a2 2 0 0 0 2-1.5L21 8H6"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  truck: '<path d="M1 4h13v12H1zM14 8h4l3 3v5h-7"/><circle cx="5.5" cy="18.5" r="2"/><circle cx="17.5" cy="18.5" r="2"/>',
  phone: '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2Z"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  gift: '<path d="M20 12v10H4V12M2 7h20v5H2zM12 22V7M12 7H7.5a2.5 2.5 0 0 1 0-5C11 2 12 7 12 7ZM12 7h4.5a2.5 2.5 0 0 0 0-5C13 2 12 7 12 7Z"/>',
  star: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1Z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  tag: '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
  users: '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 3.1a4 4 0 0 1 0 7.8M22 21a7 7 0 0 0-4-6.3"/>',
  grid: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  printer: '<path d="M6 9V2h12v7"/><rect x="2" y="9" width="20" height="9" rx="2"/><path d="M6 14h12v8H6z"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  eyeoff: '<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4M6.6 6.6A17 17 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 5.4-1.6"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
  more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
  archive: '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v13h16V8M10 12h4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  kanban: '<rect x="3" y="3" width="5" height="18" rx="1"/><rect x="10" y="3" width="5" height="12" rx="1"/><rect x="17" y="3" width="4" height="8" rx="1"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5M4 21h16"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a2 2 0 0 0 3.4 0"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 21"/>',
  play: '<path d="M7 4v16l13-8Z"/>',
  ruler: '<path d="M21.3 8.7 8.7 21.3a1 1 0 0 1-1.4 0l-4.6-4.6a1 1 0 0 1 0-1.4L15.3 2.7a1 1 0 0 1 1.4 0l4.6 4.6a1 1 0 0 1 0 1.4Z"/><path d="m7.5 10.5 2 2M10.5 7.5l2 2M13.5 4.5l2 2M4.5 13.5l2 2"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  pin: '<path d="M12 17v5M9 10.8V4h6v6.8l3 3.2H6Z"/>',
  filter: '<path d="M3 5h18l-7 8v6l-4-2v-4Z"/>',
  store: '<path d="M3 9l1.5-5h15L21 9"/><path d="M4 9v11h16V9"/><path d="M3 9a3 3 0 0 0 6 0 3 3 0 0 0 6 0 3 3 0 0 0 6 0"/>',
  msg: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z"/>',
  spool: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v6M12 15v6"/>',
  ticket: '<path d="M3 7a2 2 0 0 0 2-2h14a2 2 0 0 0 2 2v3a2 2 0 0 0 0 4v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3a2 2 0 0 0 0-4Z"/><path d="M13 5v2M13 11v2M13 17v2"/>',
  folder: '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  megaphone: '<path d="M3 11v3a1 1 0 0 0 1 1h3l6 5V5L7 10H4a1 1 0 0 0-1 1Z"/><path d="M17 8a5 5 0 0 1 0 8"/>',
  pencil: '<path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
  vibrate: '<rect x="7" y="4" width="10" height="16" rx="2"/><path d="M3 9v6M21 9v6"/>',
  percent: '<path d="M19 5 5 19"/><circle cx="6.5" cy="6.5" r="2.5"/><circle cx="17.5" cy="17.5" r="2.5"/>',
  grip: '<circle cx="9" cy="6" r="1"/><circle cx="15" cy="6" r="1"/><circle cx="9" cy="12" r="1"/><circle cx="15" cy="12" r="1"/><circle cx="9" cy="18" r="1"/><circle cx="15" cy="18" r="1"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/>',
  cube: '<path d="M21 16V8l-9-5-9 5v8l9 5Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>',
  moonglow: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/><path d="M18 2v3M16.5 3.5h3"/>',
  wallet: '<path d="M20 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h15v14H5a2 2 0 0 1-2-2V5"/><path d="M16 14h.01"/>',
  mapPin: '<path d="M12 22s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12Z"/><circle cx="12" cy="10" r="2.5"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
  wand: '<path d="m15 4-11 11 5 5 11-11Z"/><path d="M19 2v4M17 4h4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  chevDown: '<path d="m6 9 6 6 6-6"/>',
  chevR: '<path d="m9 18 6-6-6-6"/>',
};
function ic(name, cls = '') {
  const fillOnly = name === 'more';
  return `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true"${fillOnly ? ' style="fill:currentColor;stroke:none"' : ''}>${PATHS[name] || ''}</svg>`;
}
const D = window.P3D;
const PROD = Object.fromEntries(D.products.map(p => [p.id, p]));
const CAT = Object.fromEntries(D.categories.map(c => [c.id, c]));
CAT.custom = { id: 'custom', name: 'Індивідуальне', emoji: '✏️' };
const FIL = D.filaments;
const img = (p, i = 0) => (p && p.imgs && p.imgs[i]) || (p && p.imgs && p.imgs[0]) || '';
const money = n => `${Number(n).toLocaleString('uk-UA')} ₴`;
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const disc = p => (!p.contract && p.oldPrice && p.price < p.oldPrice) ? Math.round((1 - p.price / p.oldPrice) * 100) : 0;
// Акцентні кольори акцій: обираються в адмінці, застосовуються в магазині
const ACCENTS = [['blue', 'Синій'], ['orange', 'Помаранчевий'], ['violet', 'Фіолетовий'], ['green', 'Зелений'], ['teal', 'Бірюзовий'], ['pink', 'Рожевий'], ['red', 'Червоний']];
const accCls = a => a ? ` ac-${a}` : '';
const promoAcc = id => { try { return accCls((A.promos.find(p => p.id === id) || {}).accent); } catch (_) { return ''; } };
// Мітка зміни: номер у фіолетовому кружечку
const nw = (n, style = '') => `<i class="nw${style ? ' abs' : ''}"${style ? ` style="${style}"` : ''}>${n}</i>`;

// Згенеровані для прототипу опис і примітка (у реальному магазині — два необовʼязкові поля в адмінці).
// Характеристик (розмір, вага, час друку) і комплектації більше немає.
const DETAILS = {
  1: { desc: 'Спіраль, що безкінечно прокручується в пальцях. Друкується одним цілим — без збірки, тихо обертається і заспокоює краще за спінер.', note: 'Два кольори — лише в поєднаннях з фото. Хочеш іншу пару — напиши в коментарі.' },
  8: { desc: 'Мініатюрна коробка передач-антистрес: 5 передач і задня, клацає як справжня. Підходить як брелок.', note: 'Дрібні деталі — не давай дітям до 3 років.' },
  145: { desc: 'Ребриста миска для фруктів, ключів чи дрібниць. Товсті стінки, стійке дно, печатається з PLA матового кольору.', note: 'Не для гарячої їжі й посудомийки: PLA мʼякне вже від 60 °C.' },
};
const detailsFor = p => DETAILS[p.id] || {
  desc: `${p.name} — надруковано на 3D-принтері з PLA. Кожен виріб перевіряю вручну перед відправкою.`,
  note: '',
};
const REVIEWS = [
  { n: 'Олена', c: '#E96A8D', t: 'Дочка в захваті, крутить весь день. Друк акуратний, швів майже не видно.', s: 5, p: 1 },
  { n: 'Максим', c: '#4C8DF6', t: 'Швидко відправили, прийшло за день. Колір як на фото.', s: 5, p: 0 },
];

// ── Оболонка ──
let MODE = 'shop';
function setMode(m) {
  MODE = m;
  document.getElementById('mode-shop').setAttribute('aria-pressed', m === 'shop');
  document.getElementById('mode-admin').setAttribute('aria-pressed', m === 'admin');
  document.getElementById('view-shop').hidden = m !== 'shop';
  document.getElementById('view-admin').hidden = m !== 'admin';
  try { localStorage.setItem('p3d-mode', m); } catch (_) {}
  if (m === 'admin' && window.renderAdmin) renderAdmin();
  if (m === 'shop' && window.renderShop) renderShop();
  window.scrollTo({ top: 0 });
}
document.getElementById('mode-shop').onclick = () => setMode('shop');
document.getElementById('mode-admin').onclick = () => setMode('admin');
document.getElementById('markers-toggle').onchange = e => document.body.classList.toggle('hide-markers', !e.target.checked);

function effectiveDark() {
  const t = document.documentElement.getAttribute('data-theme');
  if (t) return t === 'dark';
  return matchMedia('(prefers-color-scheme: dark)').matches;
}
function paintThemeBtn() {
  document.getElementById('theme-btn').innerHTML = ic(effectiveDark() ? 'sun' : 'moon');
}
document.getElementById('theme-btn').onclick = () => {
  document.documentElement.setAttribute('data-theme', effectiveDark() ? 'light' : 'dark');
  paintThemeBtn();
  if (window.syncPhoneTheme) syncPhoneTheme();
};
paintThemeBtn();
