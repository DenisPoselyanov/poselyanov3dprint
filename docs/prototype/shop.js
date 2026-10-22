// ─────────── МАГАЗИН (Mini App) ───────────
const S = {
  screen: 'home',          // home | list | search | promos | fav | orders | cart | success
  prev: 'home',
  listKind: null,           // {type:'cat'|'coll', id}
  sort: 'pop',
  sheet: null, sheetQty: 1, sheetColor: 'black', sheetTab: 'desc', sheetAdded: false,
  cart: [ { id: 8, qty: 1, color: 'black' }, { id: 109, qty: 1, color: 'red' }, { id: 1, qty: 2, color: 'green' } ],
  fav: new Set([145, 12, 157]),
  recent: [157, 145, 11, 104],
  q: '',
  delivery: 'np', city: 'Київ', npKind: 'all', contact: false, pay: 'card', couponOpen: false, coupon: null, gift: null,
  toast: null, tgDark: null,
  coll: (() => { try { return localStorage.getItem('p3d-coll') || 'stack'; } catch (_) { return 'stack'; } })(),
  rec: (() => { try { return localStorage.getItem('p3d-rec') || 'slider'; } catch (_) { return 'slider'; } })(),
  // Подарунок окремим рядком у кошику: вигляд вибору + тимчасовий стан (розгорнуто, шторка, перегортання)
  giftStyle: (() => { try { return localStorage.getItem('p3d-gift') || 'strip'; } catch (_) { return 'strip'; } })(),
  giftOpen: false, giftSheet: false, giftIdx: 0,
  // Опис і примітка в картці товару: один вигляд на весь магазин + що заповнено (для перегляду порожніх станів)
  info: (() => { try { return localStorage.getItem('p3d-info') || 'block'; } catch (_) { return 'block'; } })(),
  infoDesc: 'short', infoNote: true, infoOpen: false,
  // Дані покупця для індивідуального товару (скидаються при відкритті картки)
  pers: { main: '', note: '', err: false },
};
// Індивідуальні товари: поле для покупця. Підпис — це custom_fields з адмінки, решта — підказки до нього.
const PERSONAL = {
  162: { label: 'Імʼя на брелоку', ph: 'Наприклад, Софія', max: 12, hint: 'Кирилиця чи латиниця, до 12 символів. Перевірю написання перед друком.' },
  163: { label: 'Імʼя на брелоку', ph: 'Наприклад, Макс', max: 10, hint: 'До 10 символів — так імʼя лишається читабельним на маленькому брелоку.' },
  101: { label: 'Номер автомобіля', ph: 'AA 1234 BB', max: 10, upper: true, hint: 'Як на номерному знаку, латиницею. Пробіли можна не ставити.' },
};
const persFor = p => p && PERSONAL[p.id];
const STEP_LABELS = ['Прийнято', 'Друкується', 'Відправлено', 'Отримано'];
const MY_ORDERS = [
  { n: 1142, date: 'сьогодні, 10:24', items: [8, 1, 109], sum: 450, step: 1, st: ['print', 'Друкується'], note: 'Орієнтовно відправка завтра' },
  { n: 1131, date: '21 вересня', items: [145], sum: 550, step: 2, st: ['ship', 'Відправлено'], ttn: '20 4509 1234 5678' },
  { n: 1098, date: '2 вересня', items: [12, 104], sum: 250, step: 4, st: ['done', 'Отримано'] },
];
const COLLECTIONS = [
  { id: 'new', name: 'Новинки', d: 'Свіжі надходження', icon: 'sparkle', h: 'var(--violet)', f: p => p.id >= 160 },
  { id: 'u100', name: 'До 100 ₴', d: 'Дрібнички за копійки', icon: 'tag', h: 'var(--green)', f: p => !p.contract && p.price <= 100 },
  { id: 'glow', name: 'Світяться вночі', d: 'Сяють у темряві', icon: 'moonglow', h: 'var(--teal)', f: p => [134, 135].includes(p.id) },
  { id: 'gift', name: 'Подарунок до 150 ₴', d: 'Ідеї, що точно сподобаються', icon: 'gift', h: 'var(--orange)', f: p => !p.contract && p.price <= 150 && p.cat !== 'custom' },
  { id: 'sale', name: 'Зі знижкою', d: 'Стара ціна й −% на картці', icon: 'percent', h: 'var(--sale)', f: p => !!disc(p) },
  { id: 'hits', name: 'Хіти', d: 'Те, що замовляють найчастіше', icon: 'flame', h: 'var(--pink)', f: p => !!p.hot },
  { id: 'top', name: 'Від 300 ₴', d: 'Великі й ефектні речі', icon: 'star', h: 'var(--amber)', f: p => !p.contract && p.price >= 300 },
];
const COLL_STYLES = [
  ['grid', 'Сітка', 'Поточний вигляд: 4 добірки в один ряд, без гортання.'],
  ['story', 'Сторіз', 'Круглі фото в кільці кольору добірки. Найкомпактніше: влізає 4–5 добірок, знайомий жест зі сторіз.'],
  ['cover', 'Обкладинки', 'Великі фото-обкладинки з назвою поверх. Найяскравіше, підходить для сезонних підбірок.'],
  ['mosaic', 'Мозаїка', 'Колаж 2×2 із товарів добірки: одразу видно, що всередині.'],
  ['stack', 'Стопка', 'Іконка, кількість товарів і мініатюри. Спокійна картка з інформацією.'],
  ['wide', 'Стрічка', 'Широкі картки з описом, кількістю і мініатюрами товарів. Добре, коли назви довгі, а добірок небагато.'],
];
const collCount = k => D.products.filter(COLLECTIONS.find(c => c.id === k).f).length;
const icTile = (name, h) => `<span class="ic-tile" style="--h:${h}">${ic(name)}</span>`;
const plural = (n, one, few, many) => { const m10 = n % 10, m100 = n % 100; return m10 === 1 && m100 !== 11 ? one : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many; };
const tovar = n => `${n} ${plural(n, 'товар', 'товари', 'товарів')}`;
// Що змінилося на кожному екрані — номери збігаються з мітками в телефоні
const NOTES = {
  home: { title: 'Головна', items: [
    ['Банер з активними акціями нагорі', 'Акції не сховані у вкладці: одразу видно прогрес до подарунка і таймер знижки.'],
    ['Категорії одним рядком', 'Прокручувані чипси з кількістю товарів замість 4 кругів, кнопки «Ще категорії» і окремої «Всі товари».'],
    ['Тематичні добірки', 'Новинки, До 100 ₴, Світяться вночі, Подарунок, Зі знижкою, Хіти, Від 300 ₴. Рядок гортається; 5 варіантів вигляду — обери праворуч.'],
    ['«Рекомендуємо» зі знижкою', 'Стара ціна і −% на картках (вже виправлено на проді). Рядок гортається; 6 варіантів вигляду — обери праворуч.'],
    ['Кнопка «+» прямо на картці', 'Товар без вибору кольору додається одним тапом, далі — степпер кількості. Тапни «+».'],
    ['Обране', 'Сердечко на кожній картці, окрема вкладка. Зберігається в Telegram CloudStorage — однаково на телефоні й ПК.'],
    ['Хіти за продажами', 'Рахуються з реальних замовлень, а не з галочки «Гарячий».'],
    ['Нещодавно переглянуті', 'Швидко повернутись до товару, який дивився вчора.'],
    ['Банер «Зробимо на замовлення»', 'Індивідуальні товари — банером, звільняє місце в нижньому меню.'],
    ['Нове нижнє меню', 'Однакові SVG-іконки замість емодзі; додані «Обране» і «Замовлення».'],
  ]},
  list: { title: 'Категорія / добірка', items: [
    ['Сортування чипсами', 'Популярні, дешевші, дорожчі, зі знижкою — без окремого вікна фільтрів.'],
    ['Кількість товарів і назва', 'Видно, де ти і скільки всього.'],
  ]},
  search: { title: 'Пошук', items: [
    ['Нещодавні запити', 'Зберігаються локально, один тап — повторити.'],
    ['Популярні запити', 'Підказки, що шукають інші.'],
    ['Розуміє транслітерацію і синоніми', 'Спробуй «фіджет» або «fidget» — знайде і спіралі, і клікери.'],
  ]},
  product: { title: 'Картка товару', items: [
    ['Кнопка «Назад» Telegram', 'BackButton замість закриття всього застосунку на Android.'],
    ['Відео в галереї', 'Легке mp4 замість важкого GIF.'],
    ['Обране і «Поділитись» на фото', 'Поділитись — через Telegram shareMessage з карткою товару.'],
    ['Рейтинг і кількість продажів', 'Соціальний доказ біля назви.'],
    ['Економія словами', '«Ти економиш 25 ₴» поруч із −%.'],
    ['Поле для індивідуальних даних', 'Лише в індивідуальних товарах: імʼя, номер авто тощо. Підпис поля задається в адмінці, є лічильник символів і необовʼязкові побажання. Без заповненого поля товар не додасться, а текст видно в кошику. Відкрий «Брелок з іменем».'],
    ['Кольори з назвою', 'Видно назву обраного кольору; кольори, що світяться, мають сяйво.'],
    ['Опис і примітка', 'Два необовʼязкові поля: опис і помітна примітка. Порожнє поле не показується. Розміри, вага й комплектація прибрані. 6 варіантів вигляду — обери праворуч.'],
    ['Прогрес до подарунка', 'Для брелків: «ще 1 брелок — і подарунок безкоштовно».'],
    ['Відгуки з фото', 'Бот просить відгук через 3 дні після отримання.'],
    ['З цим купують', 'Товари, які часто замовляють разом.'],
    ['MainButton Telegram з ціною', 'Нативна кнопка внизу, вібровідгук і анімація «летить у кошик».'],
  ]},
  cart: { title: 'Кошик і оформлення', items: [
    ['Заголовок і «Очистити»', 'Видно кількість позицій; без порожньої дірки між товарами і підсумком.'],
    ['Прогрес до знижки −10%', 'Скільки лишилось до 500 ₴ — справжня акція з адмінки.'],
    ['Подарунок окремим рядком', 'Прогрес до подарунка нагорі, а сам подарунок — рядок серед товарів за 0 ₴. 6 варіантів вибору — обери праворуч.'],
    ['Колір у кожній позиції', 'Кружечок кольору, змінюється тапом по товару.'],
    ['«Додай до замовлення»', 'Недорогі товари, щоб дотягнути до акції.'],
    ['Промокод', 'Застосований код видно окремим рядком.'],
    ['Доставка з довідників перевізників', 'Пошук міста, смт чи села з областю і районом. Для НП — відділення і поштомати, для Укрпошти — відділення з індексом. Введи «Бро» в полі міста.'],
    ['Телефон з Telegram', 'requestContact — один тап замість переписки.'],
    ['Спосіб оплати', 'Картка після підтвердження або накладений платіж.'],
    ['Підсумок з розбивкою', 'Товари, акція, промокод, доставка — все окремими рядками.'],
    ['MainButton з сумою', 'Помилки — тостами, а не системним alert().'],
  ]},
  success: { title: 'Замовлення прийнято', items: [
    ['Номер замовлення', 'Легко назвати в листуванні.'],
    ['Що буде далі', 'Етапи з орієнтовними термінами.'],
    ['Кнопки «Мої замовлення» і «Написати»', 'Замість одного «Переглянути ще товари».'],
  ]},
  orders: { title: 'Мої замовлення', items: [
    ['Історія в самому Mini App', 'Раніше була лише команда /history у боті.'],
    ['Трекер статусів', 'Прийнято → Друкується → Відправлено → Отримано. Бот надсилає повідомлення на кожному кроці.'],
    ['ТТН з копіюванням', 'Номер накладної одразу в замовленні.'],
    ['Повторити замовлення', 'Один тап — ті самі товари знову в кошику.'],
  ]},
  fav: { title: 'Обране', items: [
    ['Окрема вкладка Обране', 'Синхронізується між пристроями через CloudStorage.'],
    ['Додати все в кошик', 'Швидко перетворити список бажань на замовлення.'],
  ]},
  promos: { title: 'Акції', items: [
    ['Прогрес по кожній акції', 'Скільки лишилось до знижки чи подарунка за поточним кошиком.'],
    ['Таймер до кінця', 'Для акцій з датою завершення.'],
    ['Товари зі знижкою з сортуванням', 'Спочатку найбільша знижка.'],
  ]},
};

// ── Розрахунки кошика ──
function cartCalc() {
  const lines = S.cart.map((l, li) => ({ ...l, li, p: PROD[l.id] })).filter(l => l.p);
  const subtotal = lines.reduce((s, l) => s + (l.p.contract ? 0 : l.p.price * l.qty), 0);
  const count = lines.reduce((s, l) => s + l.qty, 0);
  const keyCount = lines.filter(l => l.p.cat === 'key').reduce((s, l) => s + l.qty, 0);
  const oldTotal = lines.reduce((s, l) => s + ((l.p.oldPrice || l.p.price) * l.qty), 0);
  const promoDisc = subtotal >= 500 ? Math.round(subtotal * 0.1) : 0;
  const couponDisc = S.coupon ? 30 : 0;
  const total = Math.max(0, subtotal - promoDisc - couponDisc);
  return { lines, subtotal, count, keyCount, oldTotal, promoDisc, couponDisc, total };
}
const cartQty = id => (S.cart.find(l => l.id === id) || {}).qty || 0;

// ── Дрібні шаблони ──
function priceHtml(p, big = false) {
  if (p.contract) return `<span class="contract-tag">Ціна після узгодження</span>`;
  const d = disc(p);
  return `<div class="price-row">${d ? `<span class="price-old">${money(p.oldPrice)}</span>` : ''}<span class="price${d ? ' sale' : ''}">${money(p.price)}</span></div>`;
}
function addControl(p) {
  const q = cartQty(p.id);
  // Договірний чи індивідуальний товар: спершу картка (конфігурація або імʼя), а не додавання наосліп
  if (p.contract || persFor(p)) return `<button class="add-btn" data-act="open" data-id="${p.id}" aria-label="Відкрити">${ic('plus')}</button>`;
  if (q) return `<div class="stepper" data-stop>
      <button data-act="dec" data-id="${p.id}" aria-label="Менше">${ic('minus')}</button><b>${q}</b>
      <button data-act="inc" data-id="${p.id}" aria-label="Більше">${ic('plus')}</button></div>`;
  return `<button class="add-btn" data-act="quick" data-id="${p.id}" aria-label="Додати в кошик">${ic('plus')}</button>`;
}
function card(p, opts = {}) {
  const d = disc(p);
  const badges = [
    d ? `<span class="b-pill sale">−${d}%</span>` : '',
    opts.hit ? `<span class="b-pill hit">${ic('flame').replace('class="i ', 'style="width:11px;height:11px" class="i ')}Хіт</span>` : '',
    p.id >= 160 && !opts.hit ? '<span class="b-pill new">Новинка</span>' : '',
    [134, 135].includes(p.id) ? '<span class="b-pill glow">Світиться</span>' : '',
  ].join('');
  return `<article class="card" data-act="open" data-id="${p.id}">
    <div class="thumb"><img src="${img(p)}" alt="${esc(p.name)}" loading="lazy">
      <div class="card-badges">${badges}</div>
      <button class="fav${S.fav.has(p.id) ? ' on' : ''}" data-act="fav" data-id="${p.id}" aria-label="В обране">${ic('heart')}</button>
      ${opts.m ? opts.m : ''}
    </div>
    <div class="card-info">
      <div class="card-name">${esc(p.name)}</div>
      <div class="card-mat">${esc(p.mat)}</div>
      <div class="card-foot">${priceHtml(p)}${addControl(p)}</div>
    </div>
  </article>`;
}
function mini(p) {
  return `<div class="mini" data-act="open" data-id="${p.id}"><div class="thumb"><img src="${img(p)}" alt="" loading="lazy"></div><p>${esc(p.name)}</p>${p.contract ? '<small class="contract-tag" style="font-size:10px">Договірна</small>' : `<span class="price">${money(p.price)}</span>`}</div>`;
}

// «Рекомендуємо»: слайдер або один із горизонтальних рядків (вигляд обирається в панелі праворуч)
const REC_STYLES = [
  ['slider', 'Слайдер', 'Поточний вигляд: по одній широкій картці на екран, зі свайпом.'],
  ['cards', 'Картки', 'Вертикальні картки як у каталозі: фото, ціна й кошик. Видно 2,5 товари.'],
  ['compact', 'Компактні', 'Невеликі горизонтальні картки: мініатюра, назва, ціна й кнопка. Багато товарів на екрані.'],
  ['poster', 'Постери', 'Високі фото-картки з ціною поверх. Найефектніше, підходить для вітрини.'],
  ['duo', 'Дві смуги', 'Два рядки, що гортаються разом: вдвічі більше товарів у тій самій висоті.'],
  ['lead', 'Головний', 'Одна велика картка й менші поруч: акцент на головному товарі тижня.'],
];
function recSection() {
  const pinned = D.products.filter(p => p.pinned);
  const list = S.rec === 'slider' || pinned.length >= 6 ? pinned
    : [...pinned, ...D.products.filter(p => !p.pinned && !p.contract && p.hot && p.imgs.length)].slice(0, 6);
  const open = p => `data-act="open" data-id="${p.id}"`;
  const saleBadge = p => disc(p) ? `<div class="card-badges"><span class="b-pill sale">−${disc(p)}%</span></div>` : '';
  const priceOnly = p => p.contract ? '<span class="contract-tag">Договірна</span>' : priceHtml(p);
  const quick = p => {
    const q = cartQty(p.id);
    return q && !p.contract && !persFor(p)
      ? `<button class="add-btn" data-act="inc" data-id="${p.id}" aria-label="Додати ще (у кошику ${q})" style="font-weight:800;font-size:14px">${q}</button>`
      : `<button class="add-btn" data-act="${p.contract || persFor(p) ? 'open' : 'quick'}" data-id="${p.id}" aria-label="Додати в кошик">${ic('plus')}</button>`;
  };
  const cell = {
    slider: p => `<div class="pin-card" style="width:calc(100% - 2 * var(--gx))" ${open(p)}>
      <div class="thumb"><img src="${img(p)}" alt="">${saleBadge(p)}</div>
      <div style="display:grid;align-content:space-between;gap:6px"><div><h4>${esc(p.name)}</h4><div class="mat">${esc(p.mat)}</div></div>
      <div style="display:flex;align-items:flex-end;justify-content:space-between;gap:6px">${priceHtml(p)}${addControl(p)}</div></div>
    </div>`,
    cards: p => card(p),
    compact: p => `<div class="rc-compact" ${open(p)}><div class="thumb"><img src="${img(p)}" alt="" loading="lazy">${saleBadge(p)}</div>
      <div class="info"><h4>${esc(p.name)}</h4><div class="foot">${priceHtml(p)}${addControl(p)}</div></div></div>`,
    poster: p => `<div class="rc-poster" ${open(p)}><img src="${img(p)}" alt="" loading="lazy">${saleBadge(p)}
      <div class="cap"><div><b>${esc(p.name)}</b><span class="pr">${p.contract ? 'Договірна' : `${disc(p) ? `<s>${money(p.oldPrice)}</s>` : ''}${money(p.price)}`}</span></div>${quick(p)}</div></div>`,
    duo: p => `<div class="rc-row" ${open(p)}><div class="thumb"><img src="${img(p)}" alt="" loading="lazy"></div>
      <div class="info"><h4>${esc(p.name)}</h4><div class="foot">${priceOnly(p)}${quick(p)}</div></div></div>`,
    lead: (p, i) => i === 0
      ? `<div class="rc-lead" ${open(p)}><div class="thumb"><img src="${img(p)}" alt="" loading="lazy">${saleBadge(p)}</div>
        <div class="body"><div><h4>${esc(p.name)}</h4><div class="mat">${esc(p.mat)}</div></div><div class="foot">${priceHtml(p)}${addControl(p)}</div></div></div>`
      : `<div class="rc-mini" ${open(p)}><div class="thumb"><img src="${img(p)}" alt="" loading="lazy">${saleBadge(p)}${quick(p)}</div><h4>${esc(p.name)}</h4>${priceOnly(p)}</div>`,
  }[S.rec] || (p => card(p));
  const cls = { slider: '', cards: ' rec-cards', duo: ' rec-duo' }[S.rec] || '';
  return `<div class="hscroll rec-row${cls}" style="gap:10px">${list.map(cell).join('')}</div>`;
}

// ── Екрани ──
function scrHome() {
  const c = cartCalc();
  const keyPct = Math.min(100, c.keyCount / 3 * 100);
  const cats = D.categories.filter(x => x.active !== false);
  const count = id => D.products.filter(p => p.cat === id).length;
  const pinned = D.products.filter(p => p.pinned);
  const hits = [8, 11, 145, 104, 109, 12].map(id => PROD[id]);
  const top = classicTop(c, keyPct, cats, count);
  return `
  <div class="sticky-search">
    <div class="search" data-act="go" data-to="search">${ic('search')}<input placeholder="Пошук: фіджет, брелок, підставка…" readonly aria-label="Пошук"><span class="filt">${ic('filter')}</span></div>
  </div>
  ${top}

  <div class="s-sec"><div class="s-h"><h3>${ic('pin')} Рекомендуємо ${nw(4)}</h3></div></div>
  ${recSection()}

  <div class="s-sec"><div class="s-h"><h3>${ic('flame')} Хіти продажів ${nw(7)}</h3><button class="link-btn" data-act="go-list" data-type="coll" data-id="u100">Усі товари</button></div></div>
  <div class="grid2">
    ${hits.map((p, i) => card(p, { hit: true, m: i === 0 ? nw(5, 'right:8px;bottom:-10px') + nw(6, 'right:44px;top:4px') : '' })).join('')}
  </div>

  <div class="s-sec"><div class="s-h"><h3>${ic('history')} Нещодавно переглянуті ${nw(8)}</h3></div></div>
  <div class="hscroll mini-row">${S.recent.map(id => mini(PROD[id])).join('')}</div>

  <div class="banner-custom" data-act="go-list" data-type="cat" data-id="custom" style="position:relative">
    ${nw(9, 'top:-6px;right:-4px')}
    <div class="ic">${ic('wand')}</div>
    <div><b>Зробимо на замовлення</b><small>Номер авто, брелок з іменем, твоя ідея</small></div>
    ${ic('back').replace('class="i ', 'style="transform:rotate(180deg);color:var(--ink-3)" class="i ')}
  </div>
  <div style="height:16px"></div>`;
}

// Мій варіант: банер-слайдер, чипси категорій, 4 добірки
function classicTop(c, keyPct, cats, count) {
  return `
  <div class="hero" style="position:relative">
    ${nw(1, 'top:-6px;right:-4px')}
    <div class="hero-track" id="hero-track">
      <div class="hero-card gift${promoAcc('gift_3_toys')}" data-act="go" data-to="promos">
        <div><div class="eyebrow">Подарунок · брелки</div><h4>Подарунок за 3 брелки</h4><p>Додай 3 брелки в кошик і обери подарунок до 100 ₴</p></div>
        <div class="hero-ico">${ic('gift')}</div>
        <div class="hero-foot"><div class="hero-prog"><i style="width:${keyPct}%"></i></div><small><b>${Math.min(c.keyCount, 3)}</b> з 3 у кошику</small></div>
      </div>
      <div class="hero-card disc deal${promoAcc('order_10_percent')}" data-act="go" data-to="promos">
        <div class="hero-top"><span class="eyebrow">Знижка на все</span><span class="timer" title="До кінця акції">${ic('clock')}<b>2д</b><b>14г</b><b>05хв</b></span></div>
        <div class="deal-main"><div class="deal-val"><b>−10</b><span>%</span></div><div><h4>Від 500 ₴ у кошику</h4><p>Рахується автоматично, без промокоду</p></div></div>
        <div class="hero-foot"><div class="hero-prog"><i style="width:${Math.min(100, c.subtotal / 5)}%"></i></div><small>${c.subtotal >= 500 ? 'Знижка діє' : `Ще <b>${money(500 - c.subtotal)}</b>`}</small></div>
      </div>
      <div class="hero-card custom" data-act="go-list" data-type="cat" data-id="custom">
        <div><div class="eyebrow">Індивідуально</div><h4>Брелок з іменем за 1 день</h4><p>Напиши ім'я — надрукую в потрібних кольорах</p></div>
        <div class="hero-ico">${ic('wand')}</div>
      </div>
    </div>
    <div class="dots-row" id="hero-dots"><i class="on"></i><i></i><i></i></div>
  </div>

  <div class="s-sec"><div class="s-h"><h3>Категорії ${nw(2)}</h3><span class="cnt">${cats.length}</span></div></div>
  <div class="hscroll">
    <button class="cat-chip" aria-pressed="true">Усі <small>${D.products.length}</small></button>
    ${cats.filter(x => x.id !== 'custom').map(x => `<button class="cat-chip" data-act="go-list" data-type="cat" data-id="${x.id}">${x.emoji} ${esc(x.name)} <small>${count(x.id)}</small></button>`).join('')}
    <button class="cat-chip" data-act="go-list" data-type="cat" data-id="custom">✏️ Індивідуальне <small>${count('custom')}</small></button>
  </div>

  <div class="s-sec"><div class="s-h"><h3>Добірки ${nw(3)}</h3></div></div>
  ${collSection()}`;
}

// Добірки: сітка або один із горизонтальних рядків (вигляд обирається в панелі праворуч)
function collSection() {
  const at = k => `data-act="go-list" data-type="coll" data-id="${k.id}" style="--h:${k.h};--h-ink:color-mix(in srgb, ${k.h} 74%, var(--ink))"`;
  const pics = (k, n) => D.products.filter(k.f).filter(p => p.imgs.length).slice(0, n);
  const cover = k => img(pics(k, 1)[0]);
  const ico = k => `<span class="ic-tile">${ic(k.icon)}</span>`;
  const cell = {
    story: k => `<button class="cl cl-story" ${at(k)}><span class="ring"><img src="${cover(k)}" alt="" loading="lazy"><span class="mk">${ic(k.icon)}</span></span><span>${k.name}</span></button>`,
    cover: k => `<button class="cl cl-cover" ${at(k)}><img src="${cover(k)}" alt="" loading="lazy">${ico(k)}<span class="cap"><b>${k.name}</b><small>${tovar(collCount(k.id))}</small></span></button>`,
    mosaic: k => { const ps = pics(k, 4); return `<button class="cl cl-mos" ${at(k)}><span class="mgrid n${ps.length}">${ps.map(p => `<img src="${img(p)}" alt="" loading="lazy">`).join('')}</span><span class="mt"><b>${k.name}</b><small>${tovar(collCount(k.id))}</small></span></button>`; },
    stack: k => { const n = collCount(k.id), ps = pics(k, 3); return `<button class="cl cl-stack" ${at(k)}><span class="top">${ico(k)}<span class="cnt">${n}</span></span><b>${k.name}</b><span class="cl-th">${ps.map(p => `<img src="${img(p)}" alt="" loading="lazy">`).join('')}${n > ps.length ? `<i>+${n - ps.length}</i>` : ''}</span></button>`; },
    wide: k => { const ps = pics(k, 3); return `<button class="cl cl-wide" ${at(k)}><span class="r">${ico(k)}<span><b>${k.name}</b><small>${k.d}</small></span></span><span class="ft"><span class="n">${tovar(collCount(k.id))}</span><span class="pv">${ps.map(p => `<img src="${img(p)}" alt="" loading="lazy">`).join('')}<span class="go">${ic('chevR')}</span></span></span></button>`; },
  }[S.coll];
  if (!cell) return `<div class="coll-grid">${COLLECTIONS.slice(0, 4).map(k => `<button class="coll" data-act="go-list" data-type="coll" data-id="${k.id}">${icTile(k.icon, k.h)}${k.name}</button>`).join('')}</div>`;
  return `<div class="hscroll coll-row">${COLLECTIONS.map(cell).join('')}</div>`;
}

function listItems() {
  const k = S.listKind;
  let items, title;
  if (k.type === 'cat') { items = D.products.filter(p => p.cat === k.id); title = `${CAT[k.id]?.emoji || ''} ${CAT[k.id]?.name || ''}`; }
  else { const c = COLLECTIONS.find(x => x.id === k.id); items = D.products.filter(c.f); title = c.name; }
  items = [...items];
  if (S.sort === 'cheap') items.sort((a, b) => (a.contract ? 1e6 : a.price) - (b.contract ? 1e6 : b.price));
  if (S.sort === 'exp') items.sort((a, b) => (b.contract ? -1 : b.price) - (a.contract ? -1 : a.price));
  if (S.sort === 'sale') items = items.filter(p => disc(p)).sort((a, b) => disc(b) - disc(a));
  return { items, title };
}
function scrList() {
  const { items, title } = listItems();
  const sorts = [['pop', 'Популярні'], ['cheap', 'Дешевші'], ['exp', 'Дорожчі'], ['sale', 'Зі знижкою']];
  return `<div class="s-sec" style="padding-top:20px"><div class="s-h"><h3 style="font-size:21px">${esc(title)}</h3><span class="cnt">${tovar(items.length)} ${nw(2)}</span></div></div>
  <div class="hscroll" style="margin-bottom:16px">${nw(1)}${sorts.map(([id, l]) => `<button class="cat-chip" data-act="sort" data-id="${id}" aria-pressed="${S.sort === id}">${l}</button>`).join('')}</div>
  ${items.length ? `<div class="grid2">${items.map(p => card(p)).join('')}</div>` : `<div class="empty"><div class="ic">${ic('search')}</div><h3>Тут поки порожньо</h3><p>Спробуй інше сортування або категорію.</p></div>`}
  <div style="height:14px"></div>`;
}

const SYN = { 'фіджет': ['fidget', 'фідж', 'спінер', 'клікер', 'спіраль'], 'fidget': ['fidget', 'фідж', 'спінер', 'клікер', 'спіраль'], 'флексі': ['flexi', 'флекс'], 'flexi': ['flexi', 'флекс'], 'брелок': ['брелок', 'брелк'], 'підставка': ['підставка'], 'дракон': ['спіраль'] };
function searchResults(q) {
  q = q.trim().toLowerCase();
  if (!q) return { items: [], extra: [] };
  const extra = SYN[q] || [];
  const terms = [q, ...extra];
  const items = D.products.filter(p => terms.some(t => (p.name + ' ' + p.mat).toLowerCase().includes(t)));
  return { items, extra };
}
function scrSearch() {
  const { items, extra } = searchResults(S.q);
  return `<div class="sticky-search"><div class="search" style="border-color:var(--blue)">${ic('search')}<input id="q" value="${esc(S.q)}" placeholder="Пошук: фіджет, брелок, підставка…" aria-label="Пошук" autocomplete="off">${S.q ? `<button class="filt" data-act="q" data-q="" aria-label="Очистити">${ic('close')}</button>` : ''}</div></div>
  ${!S.q ? `
    <div class="s-sec"><div class="s-h"><h3 style="font-size:14px">Нещодавні ${nw(1)}</h3><button class="link-btn">Очистити</button></div>
    <div style="display:flex;flex-wrap:wrap;gap:8px">${['фіджет', 'брелок з іменем', 'підставка для телефону'].map(t => `<button class="cat-chip" data-act="q" data-q="${t}">${ic('history')}${t}</button>`).join('')}</div></div>
    <div class="s-sec"><div class="s-h"><h3 style="font-size:14px">Шукають найчастіше ${nw(2)}</h3></div>
    <div style="display:flex;flex-wrap:wrap;gap:8px">${['fidget', 'флексі', 'світиться', 'органайзер', 'подарунок'].map(t => `<button class="cat-chip" data-act="q" data-q="${t}">${ic('flame')}${t}</button>`).join('')}</div></div>` : `
    <div class="s-sec"><div class="s-h"><h3 style="font-size:14px">Знайдено ${items.length}</h3></div>
    ${extra.length ? `<div class="progress-card blue" style="margin-bottom:10px"><div class="row">${nw(3)}<span>Шукаю також: <b>${extra.slice(0, 4).join(', ')}</b></span></div></div>` : ''}</div>
    ${items.length ? `<div class="grid2">${items.map(p => card(p)).join('')}</div>` : `<div class="empty"><div class="ic">${ic('search')}</div><h3>Нічого не знайшов</h3><p>Спробуй «фіджет» або «брелок». Або напиши мені — надрукую під замовлення.</p><button class="btn soft">${ic('msg')}Написати Денису</button></div>`}`}
  <div style="height:14px"></div>`;
}

function scrPromos() {
  const c = cartCalc();
  const sales = D.products.filter(p => disc(p)).sort((a, b) => disc(b) - disc(a));
  return `<div class="cart-head"><h2>Акції</h2></div>
  <div class="stack">
    <div class="progress-card orange${promoAcc('gift_3_toys')}" style="position:relative">${nw(1, 'top:-6px;right:-4px')}
      <div class="row"><span class="ic">${ic('gift')}</span><div><b>Подарунок за 3 брелки</b><br><small style="color:var(--ink-2)">${c.keyCount >= 3 ? 'Виконано — обери подарунок у кошику' : `Ще ${3 - c.keyCount} брелк${3 - c.keyCount === 1 ? 'а' : 'и'} до подарунка`}</small></div></div>
      <div class="bar"><i style="width:${Math.min(100, c.keyCount / 3 * 100)}%"></i></div>
    </div>
    <div class="progress-card blue${promoAcc('order_10_percent')}" style="position:relative">${nw(2, 'top:-6px;right:-4px')}
      <div class="row"><span class="ic">${ic('percent')}</span><div><b>−10% від 500 ₴</b><br><small style="color:var(--ink-2)">${c.subtotal >= 500 ? 'Знижка вже діє' : `Ще ${money(500 - c.subtotal)} до знижки`} · до кінця 2 дні 14 год</small></div></div>
      <div class="bar"><i style="width:${Math.min(100, c.subtotal / 5)}%"></i></div>
    </div>
  </div>
  <div class="s-sec"><div class="s-h"><h3>Зі знижкою ${nw(3)}</h3><span style="color:var(--ink-3);font-size:12px;font-weight:600">спочатку найбільша</span></div></div>
  <div class="grid2">${sales.map(p => card(p)).join('')}</div><div style="height:14px"></div>`;
}

function scrFav() {
  const items = [...S.fav].map(id => PROD[id]).filter(Boolean);
  if (!items.length) return `<div class="cart-head"><h2>Обране ${nw(1)}</h2></div><div class="empty"><div class="ic">${ic('heart')}</div><h3>Тут буде те, що сподобалось</h3><p>Тапай сердечко на товарі — список збережеться на всіх твоїх пристроях.</p><button class="btn primary" data-act="nav" data-to="home">До каталогу</button></div>`;
  return `<div class="cart-head"><h2>Обране <small>${items.length}</small> ${nw(1)}</h2><button class="link-btn" data-act="fav-all">${ic('cart')} Усе в кошик ${nw(2)}</button></div>
  <div class="grid2" style="margin-top:6px">${items.map(p => card(p)).join('')}</div><div style="height:14px"></div>`;
}

function scrOrders() {
  return `<div class="cart-head"><h2>Мої замовлення ${nw(1)}</h2></div>
  <div class="stack">${MY_ORDERS.map((o, i) => `
    <div class="order-card">
      <header><div><b>№${o.n}</b><small>${o.date} · ${money(o.sum)}</small></div><span class="st ${o.st[0]}"><i></i>${o.st[1]}</span></header>
      <div class="thumbs">${o.items.map(id => `<img src="${img(PROD[id])}" alt="${esc(PROD[id].name)}">`).join('')}</div>
      <div class="tracker" style="position:relative">${i === 0 ? nw(2, 'top:-12px;right:-6px') : ''}${STEP_LABELS.map((l, k) => `<div class="${k < o.step ? 'on' : ''}${k === o.step && o.step < 4 ? ' cur' : ''}"><i></i>${l}</div>`).join('')}</div>
      ${o.note ? `<div class="ship-line">${ic('clock')}${o.note}</div>` : ''}
      ${o.ttn ? `<div class="ttn" style="position:relative">${nw(3, 'top:-8px;right:-6px')}<span>ТТН Нової Пошти<br><b class="code">${o.ttn}</b></span><button class="btn sm soft" data-act="toast" data-msg="Номер ТТН скопійовано">${ic('copy')}Копіювати</button></div>` : ''}
      ${o.step === 4 ? `<div style="display:flex;gap:8px;position:relative">${nw(4, 'top:-8px;left:-6px')}<button class="btn sm primary" data-act="repeat" data-n="${o.n}">${ic('refresh')}Повторити</button><button class="btn sm ghost" data-act="toast" data-msg="Дякую за відгук!">${ic('star')}Залишити відгук</button></div>` : ''}
    </div>`).join('')}
  </div><div style="height:14px"></div>`;
}

// ── Подарунок окремим рядком у кошику (вигляд вибору обирається в панелі праворуч) ──
const GIFT_STYLES = [
  ['strip', 'Рядок + стрічка', 'Рядок подарунка, під ним мініатюри на вибір. Після вибору стрічка згортається в «Змінити».'],
  ['sheet', 'Шторка', 'У списку лише рядок. «Обрати» відкриває шторку знизу зі списком подарунків — кошик не стає довшим.'],
  ['cycle', 'Перегортання', 'Стрілки прямо в рядку: гортаєш подарунки на місці й тиснеш «Обрати». Найкомпактніше.'],
  ['slot', 'Порожнє місце', 'До вибору — пунктирне місце «Обери подарунок» з мініатюрами. Після — звичайний рядок, як інші товари.'],
  ['grid', 'Сітка', 'Під рядком завжди видно всі подарунки сіткою по 3, обраний підсвічений. Нічого не треба розгортати.'],
  ['radio', 'Список', 'Рядок розгортається в компактний список з кружечками вибору, без великих фото.'],
];
const giftList = () => D.products.filter(p => !p.contract && p.price <= 100 && p.cat === 'key').slice(0, 5);
// Рядок подарунка в стилі позиції кошика: фото, назва, «Подарунок», стара ціна → 0 ₴
function giftRow(g, action = '', sub = '') {
  return `<div class="gift-row">
    <div class="thumb${g ? '' : ' gift-empty'}">${g ? `<img src="${img(g)}" alt=""><span class="gift-badge">${ic('gift')}</span>` : ic('gift')}</div>
    <div><div class="ci-top"><h4>${g ? esc(g.name) : 'Обери подарунок'}</h4>${nw(3)}</div>
      <div class="ci-sub"><span class="gift-pill">Подарунок</span>${sub || (g ? 'за 3 брелки' : `до ${money(100)} · безкоштовно`)}</div>
      <div class="ci-bot"><div class="gift-act">${action}</div><div style="text-align:right">${g ? `<div class="price-old">${money(g.price)}</div>` : ''}<span class="price free">0 ₴</span></div></div></div>
  </div>`;
}
const giftOpt = p => `<button type="button" class="gift-opt" data-act="gift" data-id="${p.id}" aria-pressed="${S.gift === p.id}">
  <span class="thumb"><img src="${img(p)}" alt="">${S.gift === p.id ? `<span class="gift-check">${ic('check')}</span>` : ''}</span>
  <span class="nm">${esc(p.name)}</span><span class="pr"><s>${money(p.price)}</s> 0 ₴</span></button>`;
function giftSection() {
  const list = giftList();
  const sel = S.gift ? PROD[S.gift] : null;
  const toggle = sel ? `<button type="button" class="link-btn" data-act="gift-toggle">${S.giftOpen ? 'Згорнути' : 'Змінити'}</button>` : '';
  const open = !sel || S.giftOpen;
  switch (S.giftStyle) {
    case 'sheet':
      return `<div class="gift-card">${giftRow(sel, `<button type="button" class="btn sm soft" data-act="gift-sheet">${sel ? 'Змінити' : 'Обрати подарунок'}</button>`)}</div>`;
    case 'cycle': {
      const i = ((S.giftIdx % list.length) + list.length) % list.length;
      const cand = list[i];
      const picked = S.gift === cand.id;
      return `<div class="gift-card">${giftRow(cand, `
        <span class="gift-cyc"><button type="button" data-act="gift-step" data-d="-1" aria-label="Попередній">${ic('back')}</button><b class="tnum">${i + 1}/${list.length}</b><button type="button" data-act="gift-step" data-d="1" aria-label="Наступний">${ic('chevR')}</button></span>
        ${picked ? `<span class="gift-ok">${ic('check')}Обрано</span>` : `<button type="button" class="btn sm soft" data-act="gift" data-id="${cand.id}">Обрати</button>`}`,
        picked ? 'твій подарунок' : sel ? `обрано: ${esc(sel.name)}` : 'гортай стрілками')}</div>`;
    }
    case 'slot':
      if (open) return `<div class="gift-slot">
        <div class="gift-slot-head">${ic('gift')}<b>Обери подарунок</b>${nw(3)}<span>до ${money(100)} · 0 ₴</span></div>
        <div class="gift-grid">${list.map(giftOpt).join('')}</div></div>`;
      return `<div class="gift-card">${giftRow(sel, toggle)}</div>`;
    case 'grid':
      return `<div class="gift-card">${giftRow(sel, sel ? '' : '<span class="gift-hint">Тапни на подарунок нижче</span>')}<div class="gift-grid">${list.map(giftOpt).join('')}</div></div>`;
    case 'radio':
      return `<div class="gift-card">${giftRow(sel, toggle || '<span class="gift-hint">Обери зі списку</span>')}
        ${open ? `<div class="gift-radios">${list.map(p => `<button type="button" class="gift-radio" data-act="gift" data-id="${p.id}" aria-pressed="${S.gift === p.id}"><i></i><span>${esc(p.name)}</span><s>${money(p.price)}</s><b>0 ₴</b></button>`).join('')}</div>` : ''}</div>`;
    default: // strip
      return `<div class="gift-card">${giftRow(sel, toggle || '<span class="gift-hint">Обери нижче</span>')}
        ${open ? `<div class="hscroll gift-strip">${list.map(giftOpt).join('')}</div>` : ''}</div>`;
  }
}
// Шторка вибору подарунка (варіант «Шторка») — поверх кошика всередині телефона
function giftSheetHtml() {
  return `<div class="gsheet-back" data-act="gift-sheet-close"><div class="gsheet" data-act="noop" role="dialog" aria-label="Вибір подарунка">
    <i class="gsheet-handle"></i>
    <div class="gsheet-head"><b>Обери подарунок</b><button type="button" class="ci-rm" data-act="gift-sheet-close" aria-label="Закрити">${ic('close')}</button></div>
    <p class="gsheet-sub">Будь-який брелок до ${money(100)} — безкоштовно</p>
    <div class="gsheet-list">${giftList().map(p => `<button type="button" class="gsheet-item" data-act="gift" data-id="${p.id}" aria-pressed="${S.gift === p.id}">
      <span class="thumb"><img src="${img(p)}" alt=""></span><span class="nm">${esc(p.name)}<small><s>${money(p.price)}</s> · 0 ₴</small></span><i class="gsheet-radio"></i></button>`).join('')}</div>
  </div></div>`;
}

function scrCart() {
  const c = cartCalc();
  if (c.keyCount < 3) { S.gift = null; S.giftOpen = false; }
  if (!c.lines.length) return `<div class="cart-head"><h2>Кошик</h2></div><div class="empty"><div class="ic">${ic('cart')}</div><h3>Кошик порожній</h3><p>Почни з хітів — їх найчастіше замовляють.</p><button class="btn primary" data-act="nav" data-to="home">До каталогу</button></div>
    <div class="s-sec"><div class="s-h"><h3>Хіти</h3></div></div><div class="hscroll mini-row">${[8, 11, 145, 104].map(id => mini(PROD[id])).join('')}</div>`;
  const toPct = Math.max(0, 500 - c.subtotal);
  const upsell = [126, 134, 135, 123].map(id => PROD[id]).filter(p => p && !cartQty(p.id));
  return `<div class="cart-head"><h2>Кошик <small>${c.count}</small> ${nw(1)}</h2><button class="link-btn" style="color:var(--sale)" data-act="clear">Очистити</button></div>
  <div class="stack" style="margin-bottom:10px">
    <div class="progress-card blue${promoAcc('order_10_percent')}">
      <div class="row"><span class="ic">${ic('percent')}</span><div style="flex:1">${toPct ? `До знижки <b>−10%</b> лишилось <b>${money(toPct)}</b>` : `<b>Знижка −10% застосована</b>`}</div>${nw(2)}</div>
      <div class="bar"><i style="width:${Math.min(100, c.subtotal / 5)}%"></i></div>
    </div>
    <div class="progress-card orange${promoAcc('gift_3_toys')}">
      <div class="row"><span class="ic">${ic('gift')}</span><div style="flex:1">${c.keyCount >= 3 ? (S.gift ? '<b>Подарунок у кошику</b> — за 0 ₴' : '<b>Подарунок твій!</b> Обери його в списку товарів') : `Ще <b>${3 - c.keyCount}</b> брелок — і подарунок безкоштовно`}</div></div>
      <div class="bar"><i style="width:${Math.min(100, c.keyCount / 3 * 100)}%"></i></div>
    </div>
  </div>
  <div class="stack">
    ${c.lines.map((l, i) => { const f = FIL.find(x => x.id === l.color); return `<div class="cart-item">
      <div class="thumb" data-act="open" data-id="${l.id}"><img src="${img(l.p)}" alt=""></div>
      <div><div class="ci-top"><h4>${esc(l.p.name)}</h4><button class="ci-rm" data-act="rm" data-id="${l.id}" data-li="${l.li}" aria-label="Видалити">${ic('trash')}</button></div>
        <div class="ci-sub">${f ? `<span class="dot-c" style="background:${f.hex}"></span>${f.name}` : esc(l.p.mat)} ${i === 0 ? nw(4) : ''}</div>
        ${l.pers ? `<div class="ci-pers">${ic('pencil')}<span><b>${esc(l.pers.main)}</b>${l.pers.note ? ` · ${esc(l.pers.note)}` : ''}</span></div>` : ''}
        <div class="ci-bot"><div class="stepper"><button data-act="dec" data-id="${l.id}" data-li="${l.li}" aria-label="Менше">${ic('minus')}</button><b>${l.qty}</b><button data-act="inc" data-id="${l.id}" data-li="${l.li}" aria-label="Більше">${ic('plus')}</button></div>
        <div style="text-align:right">${disc(l.p) ? `<div class="price-old">${money(l.p.oldPrice * l.qty)}</div>` : ''}<span class="price${disc(l.p) ? ' sale' : ''}">${money(l.p.price * l.qty)}</span></div></div></div>
    </div>`; }).join('')}
    ${c.keyCount >= 3 ? giftSection() : ''}
  </div>
  ${upsell.length ? `<div class="s-sec"><div class="s-h"><h3 style="font-size:14px">Додай до замовлення ${nw(5)}</h3></div></div>
  <div class="hscroll mini-row">${upsell.map(p => `<div class="mini" style="width:118px"><div class="thumb" data-act="open" data-id="${p.id}"><img src="${img(p)}" alt=""><button class="add-btn" style="position:absolute;right:6px;bottom:6px;width:30px;height:30px;border-radius:9px" data-act="quick" data-id="${p.id}" aria-label="Додати">${ic('plus')}</button></div><p>${esc(p.name)}</p><span class="price">${money(p.price)}</span></div>`).join('')}</div>` : ''}

  <div class="section-card">
    <h4>${ic('ticket')} Промокод ${nw(6)}</h4>
    ${S.coupon ? `<div class="coupon-ok"><span>${ic('check')} <span class="code">${S.coupon}</span> · −30 ₴</span><button class="link-btn" data-act="coupon-off" style="color:inherit">Прибрати</button></div>` :
      `<div style="display:flex;gap:8px"><input class="inp" id="coupon-in" placeholder="Напр. OSIN30" value="OSIN30" style="text-transform:uppercase"><button class="btn primary" data-act="coupon">Застосувати</button></div>`}
  </div>

  <div class="section-card">
    <h4>${ic('truck')} Доставка ${nw(7)}</h4>
    <div class="opt-row">
      ${[['np', 'Нова Пошта', 'truck'], ['up', 'Укрпошта', 'package'], ['self', 'Самовивіз', 'mapPin']].map(([id, l, i]) => `<button class="opt" data-act="deliv" data-id="${id}" aria-pressed="${S.delivery === id}">${ic(i)}${l}</button>`).join('')}
    </div>
    ${S.delivery === 'self' ? `<div class="ship-line">${ic('mapPin')}Місце й час узгодимо в особистих повідомленнях</div>` : deliveryFields()}
  </div>

  <div class="section-card">
    <h4>${ic('phone')} Контакт ${nw(8)}</h4>
    ${S.contact ? `<div class="contact-ok">${ic('check')}+380 •• ••• 12 34 · з Telegram</div>` : `<button class="contact-btn" data-act="contact">${ic('phone')}Поділитись номером з Telegram</button>`}
    <div class="field"><label for="who">Ім'я та прізвище отримувача</label><input class="inp" id="who" placeholder="Як у паспорті — для НП"></div>
  </div>

  <div class="section-card">
    <h4>${ic('wallet')} Оплата ${nw(9)}</h4>
    <label class="radio"><input type="radio" name="pay" ${S.pay === 'card' ? 'checked' : ''} data-act="pay" data-id="card"><span><b>На картку після підтвердження</b><small>Надішлю реквізити, коли підтверджу замовлення</small></span></label>
    <label class="radio"><input type="radio" name="pay" ${S.pay === 'cod' ? 'checked' : ''} data-act="pay" data-id="cod"><span><b>Накладений платіж</b><small>Оплата при отриманні · комісія НП 20 ₴ + 2%</small></span></label>
  </div>

  <div class="section-card">
    <div class="field"><label for="cmt">Коментар до замовлення</label><textarea class="inp" id="cmt" rows="2" placeholder="Написи, побажання щодо кольорів…"></textarea></div>
    <div class="sum" style="position:relative">${nw(10, 'top:-8px;right:-6px')}
      <div><span>Товари (${c.count})</span><span>${money(c.oldTotal)}</span></div>
      ${c.oldTotal > c.subtotal ? `<div class="disc"><span>Знижки на товари</span><span>−${money(c.oldTotal - c.subtotal)}</span></div>` : ''}
      ${c.promoDisc ? `<div class="disc"><span>Акція −10% від 500 ₴</span><span>−${money(c.promoDisc)}</span></div>` : ''}
      ${c.couponDisc ? `<div class="disc"><span>Промокод ${S.coupon}</span><span>−${money(c.couponDisc)}</span></div>` : ''}
      ${c.keyCount >= 3 ? `<div class="disc"><span>Подарунок${S.gift ? `: ${esc(PROD[S.gift].name)}` : ''}</span><span>0 ₴</span></div>` : ''}
      <div><span>Доставка</span><span style="font-weight:600;color:var(--ink-2)">${S.delivery === 'self' ? 'безкоштовно' : 'за тарифами перевізника'}</span></div>
      <div class="total"><span>До сплати</span><span>${money(c.total)}</span></div>
    </div>
  </div>
  <div style="height:14px"></div>`;
}

// Приклад того, що повертають довідники перевізників (у реальному магазині — через наш бекенд з кешем)
const PLACES = [
  { n: 'Київ', t: 'м.', r: 'Київ', np: ['Відділення №12 · вул. Хрещатик, 22', 'Відділення №3 · вул. Велика Васильківська, 14', 'Поштомат №5821 · ТЦ Гулівер', 'Поштомат №2104 · вул. Саксаганського, 70'], up: ['01001 · вул. Хрещатик, 22', '01004 · вул. Басейна, 1/2', '01033 · вул. Саксаганського, 28'] },
  { n: 'Бровари', t: 'м.', r: 'Київська обл., Броварський р-н', np: ['Відділення №1 · вул. Київська, 253', 'Відділення №4 · бульв. Незалежності, 7', 'Поштомат №7730 · АТБ'], up: ['07400 · вул. Незалежності, 2', '07403 · вул. Гагаріна, 24'] },
  { n: 'Броди', t: 'м.', r: 'Львівська обл., Золочівський р-н', np: ['Відділення №1 · вул. Золота, 6', 'Поштомат №31220 · Сільпо'], up: ['80600 · вул. Золота, 3'] },
  { n: 'Бронники', t: 'с.', r: 'Рівненська обл., Рівненський р-н', np: ['Пункт приймання-видачі №1 · вул. Центральна, 12 (до 10 кг)'], up: ['35321 · вул. Шкільна, 4'] },
  { n: 'Брусилів', t: 'смт', r: 'Житомирська обл., Житомирський р-н', np: ['Відділення №1 · вул. Лесі Українки, 5'], up: ['12601 · вул. Незалежності, 11'] },
  { n: 'Петрівка', t: 'с.', r: 'Київська обл., Обухівський р-н', np: [], up: ['08703 · вул. Миру, 1'] },
];
const placeLabel = p => `${p.t} ${p.n}`;
function placeSuggest(q) {
  q = q.trim().toLowerCase();
  if (q.length < 2) return '';
  const list = PLACES.filter(p => p.n.toLowerCase().startsWith(q) || p.n.toLowerCase().includes(q));
  if (!list.length) return `<div class="sg-empty">Нічого не знайшов — спробуй «Бро»</div>`;
  return list.map(p => `<button type="button" data-act="place" data-n="${esc(p.n)}"><b>${placeLabel(p)}</b><small>${esc(p.r)} · ${S.delivery === 'np' ? (p.np.length ? `${p.np.length} ${plural(p.np.length, 'відділення', 'відділення', 'відділень')}` : 'немає відділень НП') : `${p.up.length} ${plural(p.up.length, 'відділення', 'відділення', 'відділень')}`}</small></button>`).join('');
}
function deliveryFields() {
  const place = PLACES.find(p => p.n === S.city) || PLACES[0];
  const np = S.delivery === 'np';
  const branches = np ? place.np.filter(b => S.npKind === 'all' || (S.npKind === 'post' ? /Поштомат/.test(b) : !/Поштомат/.test(b))) : place.up;
  return `
    <div class="field"><label for="city">Місто або село</label>
      <div style="position:relative"><input class="inp" id="city" value="${esc(placeLabel(place))}" autocomplete="off" placeholder="Почни вводити: Бро…">
      <div class="suggest" id="city-sg" hidden></div></div>
      <small class="hint-sm">${esc(place.r)}</small></div>
    ${np ? `<div class="seg-mini">${[['all', 'Усі'], ['branch', 'Відділення'], ['post', 'Поштомати']].map(([id, l]) => `<button type="button" data-act="npkind" data-id="${id}" aria-pressed="${S.npKind === id}">${l}</button>`).join('')}</div>` : ''}
    <div class="field"><label for="branch">${np ? 'Відділення або поштомат' : 'Відділення Укрпошти (індекс)'}</label>
      ${branches.length ? `<select class="inp" id="branch">${branches.map(b => `<option>${esc(b)}</option>`).join('')}</select>`
        : `<div class="ship-line" style="color:var(--amber)">${ic('alert')}${np ? 'У цьому селі немає Нової Пошти — обери Укрпошту або найближче місто' : 'Немає відділень'}</div>`}</div>`;
}

function scrSuccess() {
  return `<div class="success">
    <div class="big">${ic('check')}</div>
    <h2>Замовлення прийнято!</h2>
    <div class="num" style="position:relative">Номер <b class="code" style="color:var(--ink)">№1143</b> ${nw(1)}<br>Я напишу тобі в Telegram протягом години</div>
    <div class="section-card" style="margin:0;position:relative">${nw(2, 'top:-8px;right:-6px')}
      <ol class="timeline">
        <li class="done"><span class="tdot">${ic('check')}</span><div><b>Прийнято</b><small>Щойно</small></div></li>
        <li class="now"><span class="tdot">${ic('msg')}</span><div><b>Підтвердження і оплата</b><small>Напишу з деталями до 11:30</small></div></li>
        <li><span class="tdot">${ic('printer')}</span><div><b>Друк</b><small>1–2 дні · бот повідомить, коли почну</small></div></li>
        <li><span class="tdot">${ic('truck')}</span><div><b>Відправка Новою Поштою</b><small>Номер ТТН прийде сюди і в бот</small></div></li>
      </ol>
    </div>
    <div style="display:grid;gap:8px;position:relative">${nw(3, 'top:-8px;right:-6px')}
      <button class="btn primary" data-act="nav" data-to="orders">${ic('package')}Мої замовлення</button>
      <button class="btn ghost" data-act="nav" data-to="home">Продовжити покупки</button>
    </div>
  </div>`;
}

// ── Опис і примітка в картці товару: один вигляд на весь магазин ──
const INFO_STYLES = [
  ['block', 'Блок', 'Окремий блок «Опис» під кольорами, примітка — плашкою над текстом.'],
  ['clamp', 'Згорнутий', 'Опис обрізаний до 3 рядків з «Читати повністю». Примітка видна завжди.'],
  ['inline', 'Під ціною', 'Опис і примітка одразу під ціною, без окремого блоку. Найкоротша картка.'],
  ['accordion', 'Розгортання', 'Рядок «Опис» зі стрілкою, закритий за замовчуванням. Примітка видна одразу над ним.'],
  ['noteTop', 'Примітка зверху', 'Примітка смугою над назвою товару — її неможливо пропустити. Опис окремим блоком.'],
  ['quote', 'Цитата', 'Опис з акцентною лінією зліва, примітка дрібним рядком з іконкою під ним. Найспокійніше.'],
];
const LONG_TAIL = 'Кожен виріб друкую окремо під замовлення, тому відтінок пластику може трохи відрізнятися від фото. Потрібен інший колір чи напис — напиши в коментарі до замовлення, узгодимо до друку.';
const DEMO_NOTE = 'Друкую під замовлення: відтінок може трохи відрізнятися від фото.';
function infoData(p) {
  const det = detailsFor(p);
  const desc = S.infoDesc === 'none' ? '' : S.infoDesc === 'long' ? `${det.desc}\n\n${LONG_TAIL}` : det.desc;
  return { desc, note: S.infoNote ? (det.note || DEMO_NOTE) : '' };
}
const notePlaque = n => n ? `<div class="p-note">${ic('info')}<span>${esc(n)}</span></div>` : '';
const noteLine = n => n ? `<div class="p-note-line">${ic('info')}<span>${esc(n)}</span></div>` : '';
const descP = (t, cls = '') => t ? `<p class="desc${cls}">${esc(t)}</p>` : '';
// Повертає { top, main, block }: смуга над назвою, вставка під ціною, окремий блок
function infoParts(p) {
  const { desc, note } = infoData(p);
  const mark = nw(8, 'top:-6px;right:12px');
  if (!desc && !note) return { top: '', main: '', block: '' };
  const blk = inner => `<div class="p-block" style="position:relative">${mark}${inner}</div>`;
  const head = desc ? '<h5>Опис</h5>' : '';
  switch (S.info) {
    case 'clamp': {
      const long = desc.length > 140;
      return { top: '', main: '', block: blk(`${head}${notePlaque(note)}${descP(desc, long && !S.infoOpen ? ' clamp' : '')}${long ? `<button type="button" class="link-btn" style="justify-self:start" data-act="info-toggle">${S.infoOpen ? 'Згорнути' : 'Читати повністю'}</button>` : ''}`) };
    }
    case 'inline':
      return { top: '', main: `<div class="p-inline" style="position:relative">${mark}${noteLine(note)}${descP(desc)}</div>`, block: '' };
    case 'accordion':
      return { top: '', main: '', block: blk(`${notePlaque(note)}${desc ? `<button type="button" class="acc-head" data-act="info-toggle" aria-expanded="${S.infoOpen}"><b>Опис</b>${ic('chevDown')}</button>${S.infoOpen ? descP(desc) : ''}` : ''}`) };
    case 'noteTop':
      return { top: note ? `<div class="p-note-top" style="position:relative">${mark}${ic('info')}<span>${esc(note)}</span></div>` : '', main: '', block: desc ? blk(`${head}${descP(desc)}`) : '' };
    case 'quote':
      return { top: '', main: '', block: blk(`${descP(desc, ' quote')}${noteLine(note)}`) };
    default: // block
      return { top: '', main: '', block: blk(`${head}${notePlaque(note)}${descP(desc)}`) };
  }
}

// Персоналізація: обовʼязкове поле з підписом із адмінки + необовʼязкові побажання
function persBlock(p) {
  const f = persFor(p);
  if (!f) return '';
  const v = S.pers.main;
  return `<div class="p-block pers" style="position:relative">${nw(6, 'top:-6px;right:12px')}
    <h5>${ic('pencil')}Персоналізація</h5>
    <div class="field">
      <label for="pers-main">${esc(f.label)} <span class="req">обовʼязково</span></label>
      <div class="pers-inp"><input class="inp${S.pers.err ? ' err' : ''}" id="pers-main" maxlength="${f.max}" placeholder="${esc(f.ph)}" value="${esc(v)}" autocomplete="off"${f.upper ? ' style="text-transform:uppercase"' : ''} aria-describedby="pers-hint"><span class="pers-cnt tnum" id="pers-cnt">${v.length}/${f.max}</span></div>
      <small class="hint-sm" id="pers-hint">${S.pers.err ? `<span style="color:var(--sale);font-weight:700">Заповни поле «${esc(f.label)}», щоб додати в кошик.</span>` : esc(f.hint)}</small>
    </div>
    <div class="field">
      <label for="pers-note">Побажання <span class="opt">необовʼязково</span></label>
      <textarea class="inp" id="pers-note" rows="2" maxlength="200" placeholder="Шрифт, серденько поруч з текстом, другий рядок…">${esc(S.pers.note)}</textarea>
    </div>
  </div>`;
}

function scrProduct() {
  const p = PROD[S.sheet];
  const d = disc(p);
  const info = infoParts(p);
  const colors = FIL.filter(f => f.available);
  const cur = FIL.find(f => f.id === S.sheetColor) || colors[0];
  const c = cartCalc();
  const photos = p.imgs.length ? p.imgs : [''];
  const related = [11, 12, 119, 133, 122].filter(id => id !== p.id && PROD[id]).map(id => PROD[id]);
  const slides = [...photos.map(src => `<div><img src="${src}" alt="${esc(p.name)}"></div>`), `<div class="gal-video"><span class="play">${ic('play')}</span><small>Відео · 0:12</small></div>`];
  return `<div class="sheet"><div class="sheet-scroll" id="sheet-scroll">
    <div class="gal">
      <div class="gal-track" id="gal-track">${slides.join('')}</div>
      ${nw(2, 'left:10px;bottom:10px')}
      <div class="gal-actions">${nw(3, 'left:-10px;top:-6px')}
        <button class="${S.fav.has(p.id) ? 'on' : ''}" data-act="fav" data-id="${p.id}" aria-label="В обране">${ic('heart')}</button>
        <button data-act="toast" data-msg="Поділитись у чат Telegram" aria-label="Поділитись">${ic('share')}</button></div>
      <span class="gal-count" id="gal-count">1 / ${slides.length}</span>
    </div>
    <div class="p-main">
      ${info.top}
      <div style="font-size:12px;font-weight:700;color:var(--blue-ink)">${CAT[p.cat]?.emoji || ''} ${esc(CAT[p.cat]?.name || '')}</div>
      <h2>${esc(p.name)}</h2>
      <div class="rating"><span class="stars">${ic('star')}${ic('star')}${ic('star')}${ic('star')}${ic('star')}</span><b style="color:var(--ink)">4.9</b>· 23 відгуки · 57 продано ${nw(4)}</div>
      ${p.contract ? `<div class="contract-tag" style="justify-self:start;font-size:13px">Ціна залежить від конфігурації — порахую після замовлення</div>` : `
      <div class="p-price"><span class="price${d ? ' sale' : ''}">${money(p.price)}</span>${d ? `<span class="price-old" style="font-size:15px">${money(p.oldPrice)}</span><span class="pct">−${d}%</span><span class="save-note">Економія ${money(p.oldPrice - p.price)}</span> ${nw(5)}` : ''}</div>`}
      ${info.main}
    </div>
    ${persBlock(p)}
    ${p.filament ? `<div class="p-block">
      <h5>Колір PLA ${nw(7)}</h5>
      <div class="swatches">${colors.map(f => `<button class="swatch${/світ|glow|lum/i.test(f.name + f.id) ? ' glow' : ''}" style="background:${f.hex}" aria-pressed="${f.id === cur.id}" aria-label="${esc(f.name)}" data-act="color" data-id="${f.id}"></button>`).join('')}</div>
      <div class="swatch-name">Обрано: <b>${esc(cur.name)}</b></div>
    </div>` : ''}
    ${info.block}
    ${p.cat === 'key' ? `<div class="p-block"><div class="progress-card orange${promoAcc('gift_3_toys')}" style="margin:0">
      <div class="row"><span class="ic">${ic('gift')}</span><div style="flex:1">${c.keyCount >= 2 ? 'Додай цей брелок — і <b>подарунок безкоштовно</b>' : `Ще <b>${Math.max(0, 3 - c.keyCount)}</b> брелки до подарунка`}</div>${nw(9)}</div>
      <div class="bar"><i style="width:${Math.min(100, c.keyCount / 3 * 100)}%"></i></div></div></div>` : ''}
    <div class="p-block">
      <h5>Відгуки ${nw(10)}</h5>
      ${REVIEWS.map(r => `<div class="review"><header><span class="av" style="background:${r.c}">${r.n[0]}</span><b>${r.n}</b><span class="stars" style="color:#F5A524;display:inline-flex;margin-left:auto">${ic('star').repeat(r.s).replaceAll('class="i ', 'style="width:12px;height:12px;fill:currentColor;stroke:none" class="i ')}</span></header><p>${r.t}</p>${r.p ? `<div class="pics"><img src="${img(p, 1)}" alt=""><img src="${img(p, 0)}" alt=""></div>` : ''}</div>`).join('')}
    </div>
    <div class="p-block" style="padding-inline:0">
      <h5 style="padding-inline:16px">З цим купують ${nw(11)}</h5>
      <div class="hscroll mini-row">${related.map(mini).join('')}</div>
    </div>
    <div class="p-block" style="margin-bottom:12px">
      <div class="qty-line"><span style="font-weight:700">Кількість</span><div class="qty-big"><button data-act="sq" data-d="-1" aria-label="Менше">${ic('minus')}</button><b>${S.sheetQty}</b><button data-act="sq" data-d="1" aria-label="Більше">${ic('plus')}</button></div></div>
    </div>
  </div></div>`;
}

// ── Складання телефона ──
function mainButton() {
  if (S.screen === 'product') {
    const p = PROD[S.sheet];
    if (S.sheetAdded) return `<div class="tg-main"><button class="ghost" data-act="nav" data-to="cart">${ic('check')}В кошику · перейти до оформлення</button><div class="tg-cap">MainButton Telegram ${nw(12)}</div></div>`;
    return `<div class="tg-main"><button data-act="sheet-add">${ic('cart')}Додати в кошик${p.contract ? '' : ` · ${money(p.price * S.sheetQty)}`}</button><div class="tg-cap">MainButton Telegram · з вібровідгуком ${nw(12)}</div></div>`;
  }
  if (S.screen === 'cart' && S.cart.length) {
    return `<div class="tg-main"><button data-act="checkout">Оформити замовлення · ${money(cartCalc().total)}</button><div class="tg-cap">MainButton Telegram ${nw(11)}</div></div>`;
  }
  return '';
}
function navBar() {
  if (S.screen === 'product' || S.screen === 'success') return '';
  const c = cartCalc();
  const items = [['home', 'Каталог', 'store'], ['promos', 'Акції', 'flame'], ['fav', 'Обране', 'heart'], ['orders', 'Замовлення', 'package'], ['cart', 'Кошик', 'cart']];
  const active = ['list', 'search'].includes(S.screen) ? 'home' : S.screen;
  return `<nav class="s-nav" style="position:relative">${S.screen === 'home' ? nw(10, 'top:-9px;left:50%') : ''}${items.map(([id, l, i]) => `<button data-act="nav" data-to="${id}" ${active === id ? 'aria-current="page"' : ''}>${ic(i)}${l}${id === 'cart' && c.count ? `<span class="badge" id="cart-badge">${c.count}</span>` : ''}${id === 'fav' && S.fav.size ? `<span class="badge" style="background:var(--sale)">${S.fav.size}</span>` : ''}</button>`).join('')}</nav>`;
}
function tgHead() {
  const sub = !['home', 'promos', 'fav', 'orders', 'cart'].includes(S.screen);
  const left = sub
    ? `<button class="tg-left" data-act="back">${ic('back')}Назад ${S.screen === 'product' ? nw(1) : ''}</button>`
    : `<button class="tg-left">${ic('close')}Закрити</button>`;
  return `<div class="tg-head">${left}<div class="tg-title"><b>Poselyanov 3D Print</b><small>міні-застосунок</small></div><button class="tg-right" aria-label="Меню">${ic('more')}</button></div>`;
}
const SCREENS = { home: scrHome, list: scrList, search: scrSearch, promos: scrPromos, fav: scrFav, orders: scrOrders, cart: scrCart, success: scrSuccess, product: scrProduct };
let lastScreen = null;
function renderShop(keepScroll = true) {
  const phone = document.getElementById('phone');
  const body = phone.querySelector('.ph-body');
  const sheetScroll = phone.querySelector('#sheet-scroll');
  const st = body && keepScroll && lastScreen === S.screen ? body.scrollTop : 0;
  const sst = sheetScroll && keepScroll && lastScreen === S.screen ? sheetScroll.scrollTop : 0;
  const heroX = phone.querySelector('#hero-track')?.scrollLeft || 0;
  // Горизонтальні рядки не стрибають на початок після тапу «+» чи сердечка
  const rowX = keepScroll && lastScreen === S.screen ? [...phone.querySelectorAll('.hscroll')].map(el => el.scrollLeft) : [];
  const qFocused = document.activeElement?.id === 'q';
  phone.innerHTML = `<div class="ph-status"><span>9:41</span><span class="dots"><i></i></span></div>${tgHead()}
    <div class="ph-body">${SCREENS[S.screen]()}</div>${navBar()}${mainButton()}<div class="ph-home-ind"><i></i></div>
    ${S.screen === 'cart' && S.giftSheet ? giftSheetHtml() : ''}
    ${S.toast ? `<div class="toast">${S.toast.img ? `<img src="${S.toast.img}" alt="">` : ic('check')}<div><div>${S.toast.msg}</div>${S.toast.hap ? `<div class="hap">${ic('vibrate').replace('class="i ', 'style="width:11px;height:11px" class="i ')}HapticFeedback</div>` : ''}</div>${S.toast.act ? `<button class="t-act" data-act="nav" data-to="cart">${S.toast.act}</button>` : ''}</div>` : ''}`;
  const nb = phone.querySelector('.ph-body');
  nb.scrollTop = st;
  const ns = phone.querySelector('#sheet-scroll'); if (ns) ns.scrollTop = sst;
  const ht = phone.querySelector('#hero-track');
  if (ht) { ht.scrollLeft = heroX; ht.onscroll = () => { const i = Math.round(ht.scrollLeft / ht.clientWidth); phone.querySelectorAll('#hero-dots i').forEach((d, k) => d.classList.toggle('on', k === i)); }; }
  const gt = phone.querySelector('#gal-track');
  if (gt) gt.onscroll = () => { const i = Math.round(gt.scrollLeft / gt.clientWidth); const c = phone.querySelector('#gal-count'); if (c) c.textContent = `${i + 1} / ${gt.children.length}`; };
  const q = phone.querySelector('#q');
  if (q) { q.oninput = () => { S.q = q.value; renderShop(); }; if (qFocused || S.screen === 'search') { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }
  const city = phone.querySelector('#city'), sg = phone.querySelector('#city-sg');
  if (city && sg) {
    city.onfocus = () => city.select();
    city.oninput = () => { const html = placeSuggest(city.value); sg.innerHTML = html; sg.hidden = !html; };
    city.onblur = () => { sg.hidden = true; };
    sg.onpointerdown = e => e.preventDefault(); // інакше поле втратить фокус раніше, ніж спрацює вибір
  }
  // Поля персоналізації: стан пишеться без перемальовування, щоб не губився фокус
  const pm = phone.querySelector('#pers-main'), pn = phone.querySelector('#pers-note');
  if (pm) pm.oninput = () => {
    const f = persFor(PROD[S.sheet]);
    if (f.upper) pm.value = pm.value.toUpperCase();
    S.pers.main = pm.value;
    phone.querySelector('#pers-cnt').textContent = `${pm.value.length}/${f.max}`;
    if (S.pers.err && pm.value.trim()) { S.pers.err = false; pm.classList.remove('err'); phone.querySelector('#pers-hint').textContent = f.hint; }
    if (S.sheetAdded) { S.sheetAdded = false; phone.querySelector('.tg-main').outerHTML = mainButton(); }
  };
  if (pn) pn.oninput = () => { S.pers.note = pn.value; };
  phone.querySelectorAll('.hscroll').forEach((el, i) => { if (rowX[i]) el.scrollLeft = rowX[i]; });
  enhanceScrollers(phone);
  lastScreen = S.screen;
  renderNotes();
}

// ── Прокрутка мишею: перетягування і стрілки. На телефоні працює звичайний свайп. ──
function enhanceScrollers(root) {
  root.querySelectorAll('.hscroll, .hero-track, .gal-track').forEach(el => {
    const paged = !el.classList.contains('hscroll');   // банер і галерея гортаються по одному слайду
    const wrap = document.createElement('div');
    wrap.className = 'hs-wrap';
    if (el.style.marginBottom) { wrap.style.marginBottom = el.style.marginBottom; el.style.marginBottom = ''; }
    el.parentNode.insertBefore(wrap, el);
    wrap.append(el);
    const arrow = dir => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = `hs-arr ${dir < 0 ? 'l' : 'r'}`; b.setAttribute('aria-label', dir < 0 ? 'Назад' : 'Далі');
      b.innerHTML = ic('back');
      b.onclick = e => { e.stopPropagation(); el.scrollBy({ left: dir * (paged ? el.clientWidth : el.clientWidth * .7), behavior: 'smooth' }); };
      wrap.append(b);
    };
    arrow(-1); arrow(1);
    const sync = () => {
      wrap.classList.toggle('can-l', el.scrollLeft > 4);
      wrap.classList.toggle('can-r', el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
    };
    el.addEventListener('scroll', sync, { passive: true });
    requestAnimationFrame(sync);

    let x0 = 0, left0 = 0, down = false, moved = false;
    el.addEventListener('pointerdown', e => {
      if (e.pointerType !== 'mouse' || e.button !== 0) return;
      down = true; moved = false; x0 = e.clientX; left0 = el.scrollLeft;
    });
    el.addEventListener('pointermove', e => {
      if (!down) return;
      const dx = e.clientX - x0;
      if (!moved && Math.abs(dx) > 5) { moved = true; el.classList.add('dragging'); el.setPointerCapture(e.pointerId); }
      if (moved) el.scrollLeft = left0 - dx;
    });
    const end = e => {
      if (!down) return;
      down = false;
      if (!moved) return;
      el.classList.remove('dragging');
      if (paged) {
        const dx = e.clientX - x0, w = el.clientWidth;
        const i = Math.round(left0 / w) + (Math.abs(dx) > w * .15 ? (dx < 0 ? 1 : -1) : 0);
        el.scrollTo({ left: Math.max(0, i) * w, behavior: 'smooth' });
      }
      dragEnd = performance.now();
    };
    let dragEnd = -1e9;
    // клік одразу після перетягування не має відкривати товар
    el.addEventListener('click', ev => { if (performance.now() - dragEnd < 250) { ev.stopPropagation(); ev.preventDefault(); } }, true);
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  });
}
// Варіанти вигляду показуються лише на тій сторінці, де відповідний блок є в телефоні
function variantsFor(key) {
  const pick = (list, attr, cur, label) => `<div class="screen-picker" role="group" aria-label="${label}">${list.map(([id, l]) => `<button type="button" class="chip-btn" data-${attr}="${id}" aria-pressed="${cur === id}">${l}</button>`).join('')}</div>`;
  const about = (list, cur) => (list.find(x => x[0] === cur) || list[0])[2];
  const card = body => `<div class="notes-card layout-card"><p class="var-hint">Варіанти вигляду на цій сторінці</p>${body}</div>`;
  if (key === 'home' || key === 'list') return card(`
      <h3>Вигляд добірок</h3>
      ${pick(COLL_STYLES, 'coll', S.coll, 'Вигляд добірок')}
      <p class="notes-lead" style="font-size:13px">${about(COLL_STYLES, S.coll)}</p>
      <h3 style="margin-top:4px">Вигляд «Рекомендуємо»</h3>
      ${pick(REC_STYLES, 'rec', S.rec, 'Вигляд Рекомендуємо')}
      <p class="notes-lead" style="font-size:13px">${about(REC_STYLES, S.rec)}</p>`);
  if (key === 'product') return card(`
      <h3>Опис і примітка в картці</h3>
      ${pick(INFO_STYLES, 'info', S.info, 'Вигляд опису')}
      <p class="notes-lead" style="font-size:13px">${about(INFO_STYLES, S.info)} Один вигляд на весь магазин.</p>
      <div class="info-demo">
        <span>Опис</span><div class="seg-mini" role="group" aria-label="Опис">${[['short', 'Короткий'], ['long', 'Довгий'], ['none', 'Немає']].map(([id, l]) => `<button type="button" data-idesc="${id}" aria-pressed="${S.infoDesc === id}">${l}</button>`).join('')}</div>
        <span>Примітка</span><div class="seg-mini" role="group" aria-label="Примітка">${[['1', 'Є'], ['0', 'Немає']].map(([id, l]) => `<button type="button" data-inote="${id}" aria-pressed="${String(+S.infoNote) === id}">${l}</button>`).join('')}</div>
      </div>
      <p class="notes-lead" style="font-size:13px">Обидва поля необовʼязкові: порожнє поле в картці не показується. Характеристик, ваги й комплектації більше немає.</p>
      <div class="info-demo">
        <span>Товар</span><div class="seg-mini" role="group" aria-label="Який товар показати">${[[8, 'Звичайний'], [162, 'Індивідуальний']].map(([id, l]) => `<button type="button" data-pdemo="${id}" aria-pressed="${S.screen === 'product' && S.sheet === id}">${l}</button>`).join('')}</div>
      </div>
      <p class="notes-lead" style="font-size:13px">В індивідуальному товарі зʼявляється блок «Персоналізація» ${nw(6)} — спробуй додати в кошик з порожнім полем.</p>`);
  if (key === 'cart') return card(`
      <h3>Подарунок у кошику</h3>
      ${pick(GIFT_STYLES, 'gift', S.giftStyle, 'Вигляд подарунка')}
      <p class="notes-lead" style="font-size:13px">${about(GIFT_STYLES, S.giftStyle)} Подарунок — окремий рядок серед товарів за 0 ₴. Якщо брелків менше 3, вибір варіанта додасть їх у кошик.</p>`);
  return '';
}
function renderNotes() {
  const key = S.screen;
  const n = key === 'home' ? NOTES.home : NOTES[key];
  const picks = [['home', 'Каталог'], ['product', 'Картка товару'], ['cart', 'Кошик'], ['success', 'Після замовлення'], ['orders', 'Мої замовлення'], ['promos', 'Акції'], ['fav', 'Обране'], ['search', 'Пошук']];
  document.getElementById('shop-notes').innerHTML = `
    <div><h2>Магазин: ${n.title}</h2></div>
    <p class="notes-lead">Клікай по телефону як у Telegram. Фіолетові цифри на екрані відповідають пунктам нижче. Горизонтальні рядки гортаються мишею: перетягни або наведи й тисни стрілку.</p>
    <nav class="page-nav" aria-label="Сторінки прототипу">
      <h3>${ic('grid')}Сторінки</h3>
      <div class="screen-picker">${picks.map(([id, l]) => `<button type="button" class="page-btn" data-go="${id}" aria-pressed="${key === id || (key === 'list' && id === 'home')}">${l}</button>`).join('')}</div>
    </nav>
    ${variantsFor(key)}
    <ol class="change-list">${n.items.map(([t, d], i) => `<li><span class="nw">${i + 1}</span><div><b>${t}</b><span class="d">${d}</span></div></li>`).join('')}</ol>
    <div class="notes-card">
      <h3>Тема Telegram</h3>
      <label class="switch-label" for="tg-dark"><span class="sw"><input type="checkbox" id="tg-dark" ${phoneIsDark() ? 'checked' : ''}><span></span></span>Темна тема в телефоні</label>
      <p class="notes-lead" style="font-size:13px">Кольори беруться з themeParams Telegram, тож застосунок зливається з темою чату.</p>
    </div>
    <div class="notes-card">
      <h3>Уже виправлено на проді</h3>
      <ul class="fixed-list"><li>Адмінка знову відкривається з кнопки в боті</li><li>STL-посилання більше не видно покупцям</li><li>«Рекомендуємо» показує стару ціну і −%</li><li>У картці товару замість «Замовлення» — категорія</li></ul>
    </div>`;
  document.querySelectorAll('#shop-notes [data-go]').forEach(b => b.onclick = () => {
    const to = b.dataset.go;
    if (to === 'product') openProduct(8);
    else if (to === 'success') { S.screen = 'success'; renderShop(false); }
    else { S.screen = to; S.q = to === 'search' ? 'фіджет' : S.q; renderShop(false); }
  });
  document.querySelectorAll('#shop-notes [data-rec]').forEach(b => b.onclick = () => {
    S.rec = b.dataset.rec; S.screen = 'home';
    try { localStorage.setItem('p3d-rec', S.rec); } catch (_) {}
    renderShop(false);
  });
  document.querySelectorAll('#shop-notes [data-coll]').forEach(b => b.onclick = () => {
    S.coll = b.dataset.coll; S.screen = 'home';
    try { localStorage.setItem('p3d-coll', S.coll); } catch (_) {}
    renderShop(false);
  });
  document.querySelectorAll('#shop-notes [data-gift]').forEach(b => b.onclick = () => {
    S.giftStyle = b.dataset.gift; S.gift = null; S.giftOpen = false; S.giftSheet = false; S.giftIdx = 0;
    try { localStorage.setItem('p3d-gift', S.giftStyle); } catch (_) {}
    const kc = cartCalc().keyCount;
    if (kc < 3) addToCart(2, 3 - kc, 'green');   // щоб умова акції виконалась і рядок подарунка зʼявився
    S.screen = 'cart';
    renderShop(false);
    requestAnimationFrame(() => document.querySelector('#phone .gift-card, #phone .gift-slot')?.scrollIntoView({ block: 'center' }));
  });
  const toProduct = () => { if (S.screen === 'product') renderShop(); else openProduct(8); };
  document.querySelectorAll('#shop-notes [data-info]').forEach(b => b.onclick = () => {
    S.info = b.dataset.info; S.infoOpen = false;
    try { localStorage.setItem('p3d-info', S.info); } catch (_) {}
    toProduct();
  });
  document.querySelectorAll('#shop-notes [data-idesc]').forEach(b => b.onclick = () => { S.infoDesc = b.dataset.idesc; S.infoOpen = false; toProduct(); });
  document.querySelectorAll('#shop-notes [data-pdemo]').forEach(b => b.onclick = () => openProduct(Number(b.dataset.pdemo)));
  document.querySelectorAll('#shop-notes [data-inote]').forEach(b => b.onclick = () => { S.infoNote = b.dataset.inote === '1'; toProduct(); });
  document.getElementById('tg-dark').onchange = e => { S.tgDark = e.target.checked; syncPhoneTheme(); };
}
function phoneIsDark() { return S.tgDark == null ? effectiveDark() : S.tgDark; }
function syncPhoneTheme() {
  const ph = document.getElementById('phone');
  ph.classList.toggle('tg-dark', phoneIsDark());
  ph.classList.toggle('tg-light', !phoneIsDark());
  const t = document.getElementById('tg-dark'); if (t) t.checked = phoneIsDark();
}

// ── Дії ──
let toastTimer;
function toast(t) { S.toast = t; renderShop(); clearTimeout(toastTimer); toastTimer = setTimeout(() => { S.toast = null; renderShop(); }, 2400); }
function addToCart(id, qty = 1, color) {
  const l = S.cart.find(x => x.id === id);
  if (l) l.qty += qty; else S.cart.push({ id, qty, color: color || (PROD[id].filament ? 'black' : null) });
}
function flyFrom(el) {
  const phone = document.getElementById('phone');
  const src = el?.closest('.card, .mini, .pin-card')?.querySelector('img');
  if (!src) return;
  const pr = phone.getBoundingClientRect(), r = src.getBoundingClientRect();
  const f = document.createElement('img');
  f.src = src.src; f.className = 'fly';
  f.style.left = (r.left - pr.left + r.width / 2 - 22) + 'px'; f.style.top = (r.top - pr.top + r.height / 2 - 22) + 'px';
  phone.appendChild(f);
  const target = phone.querySelector('.s-nav button:last-child');
  const tr = target ? target.getBoundingClientRect() : { left: pr.right - 60, top: pr.bottom - 80, width: 0, height: 0 };
  requestAnimationFrame(() => {
    f.style.transform = `translate(${tr.left - r.left - r.width / 2 + tr.width / 2}px, ${tr.top - r.top - r.height / 2 + 10}px) scale(.3)`;
    f.style.opacity = '.3';
  });
  setTimeout(() => f.remove(), 650);
}
function openProduct(id) {
  S.prev = S.screen === 'product' ? S.prev : S.screen;
  S.sheet = id; S.sheetQty = 1; S.sheetTab = 'desc'; S.sheetAdded = false; S.infoOpen = false;
  S.pers = { main: '', note: '', err: false };
  S.sheetColor = (S.cart.find(l => l.id === id) || {}).color || 'black';
  S.recent = [id, ...S.recent.filter(x => x !== id)].slice(0, 6);
  S.screen = 'product';
  renderShop(false);
}
document.getElementById('phone').addEventListener('click', e => {
  const el = e.target.closest('[data-act]');
  if (!el) return;
  const a = el.dataset.act, id = Number(el.dataset.id);
  // У кошику кнопки працюють з конкретним рядком: два брелоки з різними іменами — різні позиції
  const line = el.dataset.li != null ? S.cart[Number(el.dataset.li)] : S.cart.find(x => x.id === id);
  if (a !== 'open') e.stopPropagation();
  switch (a) {
    case 'open': if (e.target.closest('[data-stop], .fav, .add-btn') && el.classList.contains('card')) return; openProduct(id); break;
    case 'nav': S.screen = el.dataset.to; renderShop(false); break;
    case 'go': S.screen = el.dataset.to; renderShop(false); break;
    case 'go-list': S.listKind = { type: el.dataset.type, id: el.dataset.id }; S.sort = 'pop'; S.screen = 'list'; renderShop(false); break;
    case 'back': S.screen = S.screen === 'product' ? (S.prev || 'home') : 'home'; renderShop(false); break;
    case 'sort': S.sort = el.dataset.id; renderShop(); break;
    case 'q': S.q = el.dataset.q; S.screen = 'search'; renderShop(); break;
    case 'fav': S.fav.has(id) ? S.fav.delete(id) : S.fav.add(id); renderShop(); break;
    case 'fav-all': S.fav.forEach(fid => { if (!PROD[fid].contract && !persFor(PROD[fid]) && !cartQty(fid)) addToCart(fid); }); toast({ msg: 'Обране додано в кошик', act: 'Кошик', hap: true }); break;
    case 'quick': {
      const p = PROD[id];
      if (p.filament && !cartQty(id) && S.screen !== 'cart') { flyFrom(el); addToCart(id, 1, 'black'); toast({ msg: `Додано: ${esc(p.name)} · чорний`, img: img(p), act: 'Кошик', hap: true }); }
      else { flyFrom(el); addToCart(id); toast({ msg: `Додано: ${esc(p.name)}`, img: img(p), act: S.screen === 'cart' ? '' : 'Кошик', hap: true }); }
      requestAnimationFrame(() => document.getElementById('cart-badge')?.classList.add('bump'));
      break;
    }
    case 'inc': if (line) line.qty++; else addToCart(id); renderShop(); break;
    case 'dec': if (line) { line.qty--; if (line.qty <= 0) S.cart = S.cart.filter(x => x !== line); } renderShop(); break;
    case 'rm': { const p = PROD[id]; S.cart = S.cart.filter(x => x !== line); toast({ msg: `Прибрано: ${esc(p.name)}`, img: img(p) }); break; }
    case 'clear': S.cart = []; S.gift = null; renderShop(); break;
    case 'color': S.sheetColor = el.dataset.id; renderShop(); break;
    case 'tab': S.sheetTab = el.dataset.id; renderShop(); break;
    case 'sq': S.sheetQty = Math.max(1, S.sheetQty + Number(el.dataset.d)); renderShop(); break;
    case 'sheet-add': {
      const p = PROD[S.sheet], f = persFor(p);
      if (f) {
        const main = S.pers.main.trim();
        if (!main) {
          S.pers.err = true;
          toast({ msg: `Вкажи «${esc(f.label.toLowerCase())}» — без цього не надрукую` });   // перемальовує телефон
          const inp = document.getElementById('pers-main');
          inp?.scrollIntoView({ block: 'center', behavior: 'smooth' }); inp?.focus({ preventScroll: true });
          break;
        }
        const pers = { main: f.upper ? main.toUpperCase() : main, note: S.pers.note.trim() };
        const color = p.filament ? S.sheetColor : null;
        const same = S.cart.find(x => x.id === p.id && x.color === color && x.pers?.main === pers.main && x.pers?.note === pers.note);
        if (same) same.qty += S.sheetQty; else S.cart.push({ id: p.id, qty: S.sheetQty, color, pers });
        S.sheetAdded = true;
        toast({ msg: `Додано: ${esc(p.name)} «${esc(pers.main)}»`, img: img(p), hap: true });
        break;
      }
      const l = S.cart.find(x => x.id === S.sheet);
      if (l) { l.qty += S.sheetQty; l.color = S.sheetColor; } else S.cart.push({ id: S.sheet, qty: S.sheetQty, color: PROD[S.sheet].filament ? S.sheetColor : null });
      S.sheetAdded = true;
      toast({ msg: `Додано ${S.sheetQty} шт у кошик`, img: img(PROD[S.sheet]), hap: true });
      break;
    }
    case 'gift': S.gift = id; S.giftOpen = false; S.giftSheet = false; renderShop(); break;
    case 'gift-toggle': S.giftOpen = !S.giftOpen; renderShop(); break;
    case 'gift-sheet': S.giftSheet = true; renderShop(); break;
    case 'gift-sheet-close': S.giftSheet = false; renderShop(); break;
    case 'gift-step': S.giftIdx += Number(el.dataset.d); renderShop(); break;
    case 'info-toggle': S.infoOpen = !S.infoOpen; renderShop(); break;
    case 'noop': break;
    case 'coupon': S.coupon = (document.getElementById('coupon-in')?.value || 'OSIN30').toUpperCase(); toast({ msg: `Промокод ${esc(S.coupon)} застосовано: −30 ₴` }); break;
    case 'coupon-off': S.coupon = null; renderShop(); break;
    case 'deliv': S.delivery = el.dataset.id; renderShop(); break;
    case 'place': S.city = el.dataset.n; renderShop(); break;
    case 'npkind': S.npKind = el.dataset.id; renderShop(); break;
    case 'contact': S.contact = true; toast({ msg: 'Номер отримано з Telegram', hap: true }); break;
    case 'pay': S.pay = el.dataset.id; renderShop(); break;
    case 'checkout':
      if (S.delivery !== 'self' && !S.contact) { toast({ msg: 'Додай номер телефону — він потрібен для Нової Пошти' }); document.querySelector('#phone .contact-btn')?.scrollIntoView({ block: 'center', behavior: 'smooth' }); return; }
      S.cart = []; S.coupon = null; S.gift = null; S.screen = 'success'; renderShop(false); break;
    case 'repeat': { const o = MY_ORDERS.find(x => x.n === Number(el.dataset.n)); o.items.forEach(i => addToCart(i)); toast({ msg: 'Товари з замовлення додано в кошик', act: 'Кошик', hap: true }); break; }
    case 'toast': toast({ msg: el.dataset.msg }); break;
  }
});
document.getElementById('phone').addEventListener('change', e => {
  const el = e.target.closest('[data-act="pay"]');
  if (el) { S.pay = el.dataset.id; }
});

syncPhoneTheme();
renderShop(false);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { paintThemeBtn(); syncPhoneTheme(); });
