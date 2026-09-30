// ─────────── АДМІНКА ───────────
const ST = {
  new: ['new', 'Нове'], ask: ['ask', 'Під питанням'], conf: ['conf', 'Підтверджене'], print: ['print', 'Друкується'],
  ready: ['ready', 'Готове'], ship: ['ship', 'Відправлене'], done: ['done', 'Отримане'], cancel: ['cancel', 'Скасоване'],
};
const FLOW = ['conf', 'print', 'ready', 'ship', 'done'];
const FLOW_LABEL = { conf: 'Підтверджено', print: 'Друк', ready: 'Готово', ship: 'Відправлено', done: 'Отримано' };
const ORDERS = [
  { n: 1143, who: 'Андрій М.', user: '@andrii_m', ago: '12 хв тому', st: 'new', items: [[145, 1, 'white', 550]], promo: 55, city: 'Львів, відділення №7', pay: 'Картка після підтвердження', phone: '+380 67 ••• 41 20' },
  { n: 1142, who: 'Олена К.', user: '@olena_k', ago: '2 год тому', st: 'print', items: [[8, 1, 'black', 100], [1, 2, 'green', 125], [109, 1, 'red', 100]], city: 'Київ, відділення №12', pay: 'Картка · оплачено', phone: '+380 93 ••• 12 34', note: 'Спіралі — одна зелена, одна салатова, якщо є' },
  { n: 1141, who: 'Марія Т.', user: '@mariia_t', ago: '5 год тому', st: 'ask', items: [[150, 1, null, 0]], contract: true, city: 'Одеса, поштомат №5821', pay: 'Накладений платіж', phone: '+380 50 ••• 77 01', comment: 'Потрібно 6 секцій, під балкон' },
  { n: 1140, who: 'Ігор П.', user: '@igor_p', ago: 'вчора', st: 'conf', items: [[104, 2, 'black', 100], [11, 1, 'red', 100], [134, 1, 'white', 50]], city: 'Дніпро, відділення №3', pay: 'Картка · оплачено', phone: '+380 97 ••• 09 88' },
  { n: 1139, who: 'Софія Л.', user: '@sofiia', ago: 'вчора', st: 'ready', items: [[157, 1, 'white', 200], [126, 4, 'white', 25]], city: 'Самовивіз', pay: 'Готівка', phone: '+380 66 ••• 31 17' },
  { n: 1131, who: 'Тарас Б.', user: '@taras_b', ago: '3 дні тому', st: 'ship', items: [[145, 1, 'black', 550]], city: 'Харків, відділення №21', pay: 'Накладений платіж', ttn: '20450912345678', phone: '+380 68 ••• 55 90' },
  { n: 1128, who: 'Наталія В.', user: '@natali_v', ago: '5 днів тому', st: 'done', items: [[12, 1, 'red', 150], [124, 1, 'white', 100]], city: 'Київ, відділення №40', pay: 'Картка · оплачено', phone: '+380 63 ••• 20 45' },
  { n: 1120, who: 'Дмитро С.', user: '@dima_s', ago: 'тиждень тому', st: 'cancel', items: [[119, 1, 'black', 200]], city: 'Вінниця, відділення №2', pay: '—', phone: '+380 95 ••• 61 02' },
];
const A = {
  page: 'overview', period: 7, oFilter: 'all', oSel: 1142, oView: 'list', tplSel: null,
  prodTab: 'active', prodSel: new Set(), prodMenu: null, prodQ: '', drawer: null, dirty: false, bulkPct: 15,
  flags: Object.fromEntries(D.products.map(p => [p.id, { hot: p.hot, pin: p.pinned, gift: !p.contract && p.price <= 100 && p.cat === 'key' }])),
  prices: Object.fromEntries(D.products.map(p => [p.id, p.price])),
  pstatus: Object.fromEntries(D.products.map(p => [p.id, [106, 160].includes(p.id) ? 'hidden' : p.id === 171 ? 'archive' : 'active'])),
  queueDone: new Set(['1140-11']), filAvail: Object.fromEntries(FIL.map(f => [f.id, f.available])),
  seg: 'buyers', bText: 'Нова добірка — брелки, що світяться в темряві ✨\nДо неділі −15% з промокодом GLOW15', bBtn: 'Переглянути',
  prodSale: false, promoTab: 'all',
  pf: null, cf: null, catf: null, filf: null, of: null,
};
// Акції й купони прототипу (заповнюються після оголошення PROMO_BLANK нижче)
function seedMarketing() {
  A.promos = [
    ...D.promotions.map(p => ({ ...PROMO_BLANK, ...p, starts: '2026-09-20', ends: p.type === 'gift_threshold' ? '' : '2026-10-01', timer: p.type !== 'gift_threshold', stats: p.type === 'gift_threshold' ? [18, 4150, '18 шт'] : [9, 5820, money(582)] })),
    { ...PROMO_BLANK, id: 'halloween_13', type: 'order_percent', title: '🎃 Моторошна знижка −13%', subtitle: 'Тільки в жовтні: −13% на все', emoji: '🎃', badge: '−13%', value: 13, accent: 'orange', starts: '2026-10-25', ends: '2026-10-31' },
    { ...PROMO_BLANK, id: 'summer_10', type: 'order_percent', title: '☀️ Літня знижка −10%', subtitle: 'Спекотні ціни: −10% на замовлення від 400 ₴', emoji: '☀️', badge: '−10%', value: 10, min_order: 400, accent: 'teal', starts: '2026-06-01', ends: '2026-08-31', timer: false, stats: [23, 9870, money(987)] },
  ];
  A.coupons = [
    { code: 'OSIN30', type: 'fixed', value: 30, min: 200, used: 12, max: 50, rev: 3840, exp: '2026-10-31', onePer: true, stack: false, sub: false, channel: '', ids: [], active: true },
    { code: 'GLOW15', type: 'percent', value: 15, min: 0, used: 4, max: 0, rev: 890, exp: '2026-10-05', onePer: true, stack: true, sub: true, channel: '@poselyanov3dprint', ids: [], active: true },
    { code: 'OLENA10', type: 'percent', value: 10, min: 0, used: 1, max: 1, rev: 495, exp: '', onePer: true, stack: false, sub: false, channel: '', ids: ['512804337'], active: true },
    { code: 'WELCOME50', type: 'fixed', value: 50, min: 300, used: 40, max: 0, rev: 11200, exp: '', onePer: true, stack: false, sub: false, channel: '', ids: [], active: false },
  ];
}
const NOTES_A = {
  overview: [
    'Нова стартова сторінка «Огляд»: цифри за період',
    'Графік виторгу по днях',
    'Воронка: запуск бота → каталог → кошик → замовлення',
    'Топ товарів за продажами',
    '«Потребує уваги»: замовлення без ціни, мало філаменту',
    'Бічне меню з лічильниками замість вкладок, що ховались за краєм',
    'Пошук по всьому (Ctrl+K) і кнопка «Відкрити магазин»',
    'Єдиний стиль з магазином замість фіолетового градієнта',
  ],
  orders: [
    'Вкладки статусів з лічильниками',
    'Пошук за номером/іменем і експорт у CSV',
    'Перемикач Список / Канбан',
    'Компактний рядок: номер, клієнт, товари, сума, статус',
    'Нові статуси по кроках; клієнт отримує повідомлення в бот',
    'Доставка і поле для ТТН',
    'Позиції з кольорами; договірну ціну вводиш прямо тут',
    'Собівартість і прибуток по замовленню',
    'Шаблони повідомлень клієнту',
    'Внутрішня нотатка (клієнт не бачить)',
    'Видалення сховане в меню «⋯», а не червона кнопка на всю ширину',
    'Кнопка «Замовлення вручну» — для замовлень з Instagram, OLX чи особисто',
  ],
  queue: [
    'Черга друку: всі підтверджені замовлення в одному списку',
    'Групування за кольором — менше перезаправок',
    'Час друку і витрата пластику',
    'Відмітка «надруковано» → замовлення саме стає «Готове»',
  ],
  products: [
    'Вкладки Активні / Приховані / Архів з відновленням',
    'Компактна таблиця замість карток по 400 px',
    'Ціна редагується прямо в рядку',
    'Хіт / Закріплений / Подарунок — перемикачі в рядку',
    'Перегляди, додавання в кошик, продажі',
    'Меню «⋯»: дублювати, приховати, в архів',
    'Масові дії: знижка %, категорія, приховати',
    'Тапни рядок — відкриється новий редактор',
    'Кнопка «Товар» — лише тут, а не на кожній сторінці',
    'Фільтр «Акційні» — товари зі старою ціною',
  ],
  categories: [
    'Кнопка «Категорія»: форма з емодзі, ID з назви, слотом і видимістю',
    'Порядок — перетягуванням',
    'Кількість товарів у кожній',
    'Слот на головній обирається тут же',
    'Приховати категорію без видалення',
  ],
  filaments: [
    'Кнопка «Колір»: назва, HEX, світний',
    '«Закінчився» одним перемикачем',
    'Скільки товарів використовує колір',
    'Позначка для кольорів, що світяться',
  ],
  coupons: [
    'Кнопка «Купон»: %, ₴, ліміти, одноразовий, сумування з акцією, лише підписникам, доступ за Telegram ID',
    'Посилання, яке саме застосовує купон — для поста в канал',
    'Використання і виторг з купона',
    'Умови купона видно на картці; тапни картку — редагування',
  ],
  promos: [
    'Кнопка «Акція»: 16 готових шаблонів і 6 типів акцій',
    'Превʼю банера точно як у магазині',
    'Таймер до кінця акції в магазині',
    'Розклад: старт і завершення, вкладки Активні / Заплановані / Завершені',
    'Результат: скільки замовлень і виторгу дала акція',
    'Знижки на окремі товари — окремим блоком з переходом у Товари',
  ],
  clients: [
    'Сегменти клієнтів',
    'Таблиця з сумою, замовленнями і останньою активністю',
    'Персональний купон з рядка клієнта',
    'Написати клієнту в один клік',
    'Експорт клієнтів у CSV',
  ],
  broadcast: [
    'Аудиторія за сегментом з кількістю отримувачів',
    'Текст, фото і кнопка на товар',
    'Превʼю як у Telegram',
    'Відкласти відправку',
    'Історія розсилок: доставлено, переходи, замовлення',
    'Кнопка «Нова розсилка» очищає форму',
  ],
};
// Головна дія кожної сторінки — в правому верхньому куті. m — номер мітки поруч із кнопкою.
const PAGE_ACTION = {
  orders: { label: 'Замовлення вручну', icon: 'plus', a: 'new', v: 'order', m: 12 },
  products: { label: 'Товар', icon: 'plus', a: 'new', v: 'product', m: 9 },
  categories: { label: 'Категорія', icon: 'plus', a: 'new', v: 'category', m: 1 },
  filaments: { label: 'Колір', icon: 'plus', a: 'new', v: 'filament', m: 1 },
  promos: { label: 'Акція', icon: 'plus', a: 'new', v: 'promo', m: 1 },
  coupons: { label: 'Купон', icon: 'plus', a: 'new', v: 'coupon', m: 1 },
  clients: { label: 'Експорт CSV', icon: 'download', a: 'toast', v: 'Файл clients-2026-09.csv сформовано', m: 5, ghost: true },
  broadcast: { label: 'Нова розсилка', icon: 'plus', a: 'bnew', v: '', m: 6 },
};

// ── Акції: типи і шаблони (ті самі, що в теперішній адмінці, + 2 нові типи) ──
const TODAY = '2026-09-28';
const PROMO_TYPES = [
  ['order_percent', '💸', 'Знижка % на суму', 'На весь кошик від мінімальної суми'],
  ['order_fixed', '💵', 'Знижка ₴ на суму', 'Фіксована сума від порогу'],
  ['gift_threshold', '🎁', 'Подарунок за N товарів', 'N товарів з категорії → подарунок до X ₴'],
  ['info', '📣', 'Банер без знижки', 'Доставка, відгук за купон, день народження'],
  ['cat_percent', '🏷️', 'Знижка на категорію', 'Напр. −15% на всі брелки', true],
  ['free_ship', '🚚', 'Безкоштовна доставка', 'Від суми — рахується в кошику сама', true],
];
const PRESETS = [
  ['Базові', [
    ['order_10', '−10% від 500 ₴', { type: 'order_percent', title: '−10% на замовлення', subtitle: 'При замовленні від 500 ₴ отримай знижку 10% на всю суму', emoji: '🔖', badge: '−10%', value: 10, min_order: 500, priority: 20 }],
    ['gift_3', 'Подарунок за 3 брелки', { type: 'gift_threshold', title: 'Безкоштовний подарунок', subtitle: 'Додай 3+ брелки в кошик і обери подарунок на свій смак', emoji: '🎁', category: 'key', min_items: 3, max_gift_price: 100, priority: 10 }],
    ['first', 'Перше замовлення −50 ₴', { type: 'order_fixed', title: '−50 ₴ на перше замовлення', subtitle: 'Знайомимось! Мінус 50 ₴ на замовлення від 300 ₴', emoji: '👋', badge: '−50 ₴', value: 50, min_order: 300, priority: 15 }],
    ['big', 'Великий кошик −15%', { type: 'order_percent', title: '−15% на великі замовлення', subtitle: 'Замовляй від 1000 ₴ і отримуй 15% знижки', emoji: '📦', badge: '−15%', value: 15, min_order: 1000, priority: 25 }],
    ['weekend', 'Вихідні −10%', { type: 'order_percent', title: '🎉 Знижка вихідного дня', subtitle: 'Вмикай на вихідні: −10% на замовлення від 300 ₴', emoji: '🎉', badge: '−10%', value: 10, min_order: 300, priority: 12 }],
    ['gift_4', '4-й товар — подарунок', { type: 'gift_threshold', title: 'Четвертий товар — подарунок', subtitle: 'Збери 4 різні товари в кошику й обери подарунок', emoji: '🎁', category: '', min_items: 4, max_gift_price: 150, priority: 12 }],
  ]],
  ['Свята', [
    ['newyear', 'Новорічна −20%', { type: 'order_percent', title: '🎄 Новорічна знижка −20%', subtitle: 'Готуємось до свят: −20% на замовлення від 600 ₴', emoji: '🎄', badge: '−20%', value: 20, min_order: 600, priority: 5 }],
    ['nikolay', 'Подарунок до Миколая', { type: 'gift_threshold', title: '🎅 Подарунок від Миколая', subtitle: 'Обери 2+ іграшки — і Миколай додасть подарунок у кошик', emoji: '🎅', category: 'toy', min_items: 2, max_gift_price: 120, priority: 5 }],
    ['valentine', 'День закоханих −14%', { type: 'order_percent', title: '💝 −14% до Дня закоханих', subtitle: 'Знижка 14% на все з 10 по 14 лютого', emoji: '💝', badge: '−14%', value: 14, min_order: 0, priority: 5 }],
    ['march8', '8 березня −10%', { type: 'order_percent', title: '🌷 Весняна знижка −10%', subtitle: 'Даруй приємне: −10% на всі замовлення', emoji: '🌷', badge: '−10%', value: 10, min_order: 0, priority: 5 }],
    ['halloween', 'Хелловін −13%', { type: 'order_percent', title: '🎃 Моторошна знижка −13%', subtitle: 'Тільки в жовтні: −13% на все', emoji: '🎃', badge: '−13%', value: 13, min_order: 0, priority: 5 }],
  ]],
  ['Сезонні', [
    ['school', 'До школи −12%', { type: 'order_percent', title: '🎒 До школи −12%', subtitle: 'Органайзери, брелки та дрібнички зі знижкою 12%', emoji: '🎒', badge: '−12%', value: 12, min_order: 400, priority: 8 }],
    ['summer', 'Літня −10% від 400 ₴', { type: 'order_percent', title: '☀️ Літня знижка −10%', subtitle: 'Спекотні ціни: −10% на замовлення від 400 ₴', emoji: '☀️', badge: '−10%', value: 10, min_order: 400, priority: 8 }],
  ]],
  ['Інформаційні', [
    ['free_delivery', 'Безкоштовна доставка', { type: 'info', title: '🚚 Безкоштовна доставка', subtitle: 'Ми оплачуємо Нову пошту при замовленні від 700 ₴', emoji: '🚚', priority: 30 }],
    ['review', 'Відгук = купон', { type: 'info', title: '⭐ Відгук — і купон твій', subtitle: 'Надішли фото товару у відгуку і отримай персональний купон −50 ₴', emoji: '⭐', priority: 35 }],
    ['birthday', 'День народження', { type: 'info', title: '🎂 Твій день народження', subtitle: 'Напиши нам у місяць свого дня народження — надішлемо персональний купон', emoji: '🎂', priority: 35 }],
  ]],
];
const PROMO_BLANK = { accent: '', type: 'order_percent', title: '', subtitle: '', emoji: '🔥', badge: '', value: 10, min_order: 0, category: '', min_items: 3, max_gift_price: 100, starts: TODAY, ends: '', priority: 100, active: true, timer: true };
const promoStatus = p => !p.active ? 'off' : p.starts && p.starts > TODAY ? 'scheduled' : p.ends && p.ends < TODAY ? 'ended' : 'active';
const PROMO_ST = { active: ['ready', 'Діє'], scheduled: ['new', 'Запланована'], ended: ['done', 'Завершена'], off: ['cancel', 'Вимкнена'] };
const heroCls = t => t === 'gift_threshold' ? 'gift' : t === 'info' ? 'custom' : t === 'free_ship' ? 'ship' : 'disc';
const heroIc = t => ({ gift_threshold: 'gift', info: 'megaphone', free_ship: 'truck', cat_percent: 'tag', order_fixed: 'wallet' }[t] || 'percent');
function promoCond(p) {
  const cat = p.category ? `«${CAT[p.category]?.name || p.category}»` : 'будь-яких';
  switch (p.type) {
    case 'order_percent': return `−${p.value}% на суму${p.min_order ? ` від ${money(p.min_order)}` : ''}`;
    case 'order_fixed': return `−${money(p.value)} на суму${p.min_order ? ` від ${money(p.min_order)}` : ''}`;
    case 'gift_threshold': return `${p.min_items}+ товарів ${cat} → подарунок до ${money(p.max_gift_price)}`;
    case 'cat_percent': return `−${p.value}% на категорію ${cat}`;
    case 'free_ship': return `Безкоштовна доставка від ${money(p.min_order)}`;
    default: return 'Без автоматичної знижки';
  }
}
function promoHero(p, extra = '') {
  const timer = p.timer && p.ends ? `<span class="timer" title="До кінця акції">${ic('clock')}<b>2д</b><b>14г</b><b>05хв</b></span>` : '';
  if (['order_percent', 'order_fixed', 'cat_percent'].includes(p.type)) {
    const val = p.type === 'order_fixed' ? `<b>−${Number(p.value) || 0}</b><span>₴</span>` : `<b>−${Number(p.value) || 0}</b><span>%</span>`;
    return `<div class="hero-card ${heroCls(p.type)}${accCls(p.accent)} deal" style="min-height:124px">${extra}
      <div class="hero-top"><span class="eyebrow">${p.type === 'cat_percent' ? 'Знижка на категорію' : 'Знижка на все'}</span>${timer}</div>
      <div class="deal-main"><div class="deal-val">${val}</div><div><h4>${esc(p.title || 'Назва акції')}</h4><p>${esc(p.subtitle || 'Коротко про умови')}</p></div></div>
      ${p.min_order ? `<div class="hero-foot"><div class="hero-prog"><i style="width:40%"></i></div><small>Ще <b>${money(Math.round(p.min_order * .6))}</b></small></div>` : ''}</div>`;
  }
  return `<div class="hero-card ${heroCls(p.type)}${accCls(p.accent)}" style="min-height:124px">${extra}<div><div class="eyebrow">${p.emoji ? esc(p.emoji) + ' ' : ''}Акція</div><h4>${esc(p.title || 'Назва акції')}</h4><p>${esc(p.subtitle || 'Коротко про умови')}</p></div><div class="hero-ico">${ic(heroIc(p.type))}</div>
    ${timer ? `<div class="hero-foot">${timer}</div>` : p.type === 'gift_threshold' ? `<div class="hero-foot"><div class="hero-prog"><i style="width:33%"></i></div><small>1 з ${p.min_items}</small></div>` : ''}</div>`;
}
seedMarketing();
const NAV = [
  ['g', 'Робота'],
  ['overview', 'Огляд', 'chart'],
  ['orders', 'Замовлення', 'package', () => ORDERS.filter(o => ['new', 'ask'].includes(o.st)).length, true],
  ['queue', 'Черга друку', 'printer', () => queueRows().filter(r => !A.queueDone.has(r.key)).length],
  ['g', 'Каталог'],
  ['products', 'Товари', 'cube', () => D.products.length],
  ['categories', 'Категорії', 'folder'],
  ['filaments', 'Філаменти', 'spool'],
  ['g', 'Маркетинг'],
  ['promos', 'Акції', 'flame'],
  ['coupons', 'Купони', 'ticket'],
  ['clients', 'Клієнти', 'users'],
  ['broadcast', 'Розсилки', 'megaphone'],
];
const TITLES = { overview: 'Огляд', orders: 'Замовлення', queue: 'Черга друку', products: 'Товари', categories: 'Категорії', filaments: 'Філаменти', promos: 'Акції', coupons: 'Купони', clients: 'Клієнти', broadcast: 'Розсилки' };
const orderSum = o => o.items.reduce((s, [, q, , p]) => s + q * p, 0) - (o.promo || 0);
const statFor = id => { const v = (id * 37) % 380 + 60; return { v, c: Math.round(v * (.12 + (id % 7) / 50)), s: Math.round(v * (.05 + (id % 5) / 60)) }; };
const filName = id => (FIL.find(f => f.id === id) || {}).name || '—';
const filHex = id => (FIL.find(f => f.id === id) || {}).hex || '#ccc';

// ── Огляд ──
function pgOverview() {
  const rev = A.period === 1 ? [0, 0, 0, 0, 0, 0, 0, 0, 120, 0, 450, 0, 0, 495] : A.period === 7 ? [320, 150, 0, 610, 450, 275, 980, 200, 530, 125, 700, 450, 350, 495] : [520, 830, 460, 1200, 940, 610, 1480, 880, 1050, 720, 1320, 990, 760, 1180];
  const k = A.period === 1 ? { r: 945, o: 3, a: 315, c: 7.1 } : A.period === 7 ? { r: 3750, o: 14, a: 268, c: 9.8 } : { r: 14220, o: 51, a: 279, c: 10.4 };
  const max = Math.max(...rev, 1);
  const W = 640, H = 210, pl = 40, pb = 24, bw = (W - pl - 10) / rev.length;
  const ticks = [0, .5, 1].map(t => Math.round(max * t / 100) * 100);
  const days = ['15', '16', '17', '18', '19', '20', '21', '22', '23', '24', '25', '26', '27', '28'];
  const bars = rev.map((v, i) => { const h = (v / max) * (H - pb - 16); return `<rect x="${pl + i * bw + 4}" y="${H - pb - h}" width="${bw - 8}" height="${Math.max(h, 1.5)}" rx="4" fill="${i === rev.length - 1 ? 'var(--blue)' : 'color-mix(in srgb, var(--blue) 45%, transparent)'}"><title>${days[i]} вер: ${money(v)}</title></rect>`; }).join('');
  const grid = ticks.map(t => { const y = H - pb - (t / max) * (H - pb - 16); return `<line x1="${pl}" x2="${W - 6}" y1="${y}" y2="${y}" stroke="var(--line)"/><text x="${pl - 6}" y="${y + 3}" text-anchor="end">${t >= 1000 ? (t / 1000).toFixed(1) + 'к' : t}</text>`; }).join('');
  const labels = days.map((d, i) => i % 2 === 0 || i === days.length - 1 ? `<text x="${pl + i * bw + bw / 2}" y="${H - 6}" text-anchor="middle">${d}</text>` : '').join('');
  const top = [[8, 23], [145, 9], [11, 17], [109, 15], [104, 12]];
  const tmax = 23;
  return `
  <div class="toolbar" style="justify-content:space-between">
    <div class="seg" role="group" aria-label="Період">${[[1, 'Сьогодні'], [7, '7 днів'], [30, '30 днів']].map(([v, l]) => `<button data-a="period" data-v="${v}" aria-pressed="${A.period === v}">${l}</button>`).join('')}</div>
    <span style="color:var(--ink-3);font-size:12.5px">Оновлено щойно ${nw(1)}</span>
  </div>
  <div class="kpis">
    <div class="kpi"><span class="l">${ic('wallet')}Виторг</span><span class="v">${money(k.r)}</span><span class="d up">↑ 18% до попер. періоду</span></div>
    <div class="kpi"><span class="l">${ic('package')}Замовлень</span><span class="v">${k.o}</span><span class="d up">↑ 4</span></div>
    <div class="kpi"><span class="l">${ic('tag')}Середній чек</span><span class="v">${money(k.a)}</span><span class="d down">↓ 3%</span></div>
    <div class="kpi"><span class="l">${ic('users')}Конверсія в замовлення</span><span class="v">${k.c}%</span><span class="d up">↑ 1,2 п.п.</span></div>
  </div>
  <div class="cols-2">
    <div class="panel"><h3>Виторг по днях ${nw(2)}<small>вересень</small></h3>
      <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Виторг по днях">${grid}${bars}${labels}</svg></div>
    <div class="panel"><h3>Воронка ${nw(3)}<small>за ${A.period === 1 ? 'сьогодні' : A.period + ' днів'}</small></h3>
      <div class="funnel">
        ${[['Запустили бота', 412], ['Відкрили каталог', 268], ['Додали в кошик', 97], ['Оформили', 41]].map(([l, v], i, arr) => `<div class="fr"><span>${l}</span><div class="fb"><i style="width:${v / 412 * 100}%;opacity:${1 - i * .18}"></i></div><b>${v}</b></div>${i < arr.length - 1 ? `<div class="conv">→ ${Math.round(arr[i + 1][1] / v * 100)}% далі</div>` : ''}`).join('')}
      </div>
      <p style="margin:12px 0 0;font-size:12.5px;color:var(--ink-2)">Найбільша втрата — між кошиком і оформленням. Прогрес до акцій у кошику має це покращити.</p>
    </div>
  </div>
  <div class="cols-2">
    <div class="panel"><h3>Потребує уваги ${nw(5)}</h3>
      <div class="alert-row info">${ic('package')}<span><b>1 нове замовлення</b> чекає підтвердження</span><button class="btn sm soft" data-a="go" data-v="orders">Відкрити</button></div>
      <div class="alert-row warn">${ic('msg')}<span><b>№1141</b> з договірною ціною — вкажи суму</span><button class="btn sm soft" data-a="order" data-v="1141">Вказати</button></div>
      <div class="alert-row warn">${ic('eyeoff')}<span><b>Жовтий</b> закінчився — прихований у 29 товарах</span><button class="btn sm soft" data-a="go" data-v="filaments">Поповнити</button></div>
    </div>
    <div class="panel"><h3>Топ товарів ${nw(4)}<small>продано, шт</small></h3>
      <div class="top-list">${top.map(([id, n]) => `<div class="tr"><img src="${img(PROD[id])}" alt=""><div><div style="font-weight:600">${esc(PROD[id].name)}</div><div class="tb"><i style="width:${n / tmax * 100}%"></i></div></div><b class="tnum">${n}</b></div>`).join('')}</div>
    </div>
  </div>`;
}

// ── Замовлення ──
function orderRowsFiltered() {
  return ORDERS.filter(o => A.oFilter === 'all' || o.st === A.oFilter || (A.oFilter === 'work' && ['conf', 'print', 'ready'].includes(o.st)));
}
function pgOrders() {
  const counts = s => ORDERS.filter(o => o.st === s).length;
  const tabs = [['all', 'Усі', ORDERS.length], ['new', 'Нові', counts('new')], ['ask', 'Під питанням', counts('ask')], ['work', 'В роботі', ORDERS.filter(o => ['conf', 'print', 'ready'].includes(o.st)).length], ['ship', 'Відправлені', counts('ship')], ['done', 'Отримані', counts('done')], ['cancel', 'Скасовані', counts('cancel')]];
  const list = orderRowsFiltered();
  const head = `<div class="toolbar" style="justify-content:space-between">
    <div class="tabs-line">${nw(1)}${tabs.map(([id, l, c]) => `<button class="tab-pill" data-a="ofilter" data-v="${id}" aria-pressed="${A.oFilter === id}">${l}<span class="c">${c}</span></button>`).join('')}</div>
    <div class="toolbar">${nw(2)}<input class="a-input" placeholder="№ або ім'я клієнта" aria-label="Пошук замовлень" style="width:180px"><button class="btn sm ghost" data-a="toast" data-v="Файл orders-2026-09.csv сформовано">${ic('download')}CSV</button>
      ${nw(3)}<div class="seg" role="group" aria-label="Вигляд"><button data-a="oview" data-v="list" aria-pressed="${A.oView === 'list'}">${ic('list')}Список</button><button data-a="oview" data-v="kanban" aria-pressed="${A.oView === 'kanban'}">${ic('kanban')}Канбан</button></div></div>
  </div>`;
  if (A.oView === 'kanban') {
    const cols = [['new', 'Нові'], ['conf', 'Підтверджені'], ['print', 'Друкуються'], ['ready', 'Готові'], ['ship', 'Відправлені']];
    return head + `<div class="kanban">${cols.map(([s, l]) => { const os = ORDERS.filter(o => o.st === s || (s === 'new' && o.st === 'ask')); return `<div class="kcol"><h4>${l}<span class="st ${ST[s][0]}">${os.length}</span></h4>${os.map(o => `<div class="kcard" data-a="order" data-v="${o.n}"><div style="display:flex;justify-content:space-between"><b>№${o.n}</b><span style="color:var(--ink-3)">${o.ago}</span></div><div>${esc(o.who)}</div><div class="thumbs">${o.items.map(([id]) => `<img src="${img(PROD[id])}" alt="">`).join('')}</div><div style="display:flex;justify-content:space-between;align-items:center"><b class="tnum">${o.contract ? 'договірна' : money(orderSum(o))}</b>${o.st === 'ask' ? '<span class="st ask">питання</span>' : ''}</div></div>`).join('') || '<small style="color:var(--ink-3)">Порожньо</small>'}</div>`; }).join('')}</div>`;
  }
  const sel = ORDERS.find(o => o.n === A.oSel) || list[0];
  return head + `<div class="order-layout">
    <div class="panel" style="padding:0;overflow:hidden;position:relative">${nw(4, 'top:8px;right:8px')}
      ${list.map(o => `<div class="o-row${sel && o.n === sel.n ? ' sel' : ''}" data-a="order" data-v="${o.n}">
        <div class="onum">№${o.n}<small>${o.ago}</small></div>
        <div class="who"><b>${esc(o.who)}</b><small>${o.items.map(([id, q]) => `${esc(PROD[id].name)}${q > 1 ? ' ×' + q : ''}`).join(', ')}</small></div>
        <div class="osum">${o.contract ? '<span class="contract-tag">ціна?</span>' : money(orderSum(o))}</div>
        <span class="st ${ST[o.st][0]}"><i></i>${ST[o.st][1]}</span>
      </div>`).join('') || '<div class="empty"><p>Замовлень з таким статусом немає</p></div>'}
    </div>
    ${sel ? orderDetail(sel) : ''}
  </div>`;
}
function orderDetail(o) {
  const idx = FLOW.indexOf(o.st);
  const sum = orderSum(o);
  const grams = o.items.reduce((s, [id, q]) => s + q * (id === 145 ? 210 : 25), 0);
  const cost = Math.round(grams * 0.9 + o.items.length * 8);
  const tpls = [['pay', 'Реквізити для оплати', `Привіт, ${o.who.split(' ')[0]}! Замовлення №${o.n} підтверджено 🙌\nСума: ${money(sum)}\nКартка monobank: 5375 •••• •••• 1234\nПісля оплати одразу беру в друк.`], ['ready', 'Готово до відправки', `Замовлення №${o.n} надруковано і запаковано! Сьогодні відправлю Новою Поштою.`], ['ttn', 'Номер ТТН', `Відправлено! ТТН: 20450912345678\nВідстежити: novaposhta.ua`], ['review', 'Попросити відгук', `Як тобі замовлення №${o.n}? Буду вдячний за відгук і фото 💙`]];
  const tpl = tpls.find(t => t[0] === A.tplSel);
  return `<div class="o-detail">
    <div class="panel" style="display:grid;gap:12px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
        <div><div style="font-size:18px;font-weight:800">№${o.n} <span class="st ${ST[o.st][0]}" style="vertical-align:middle"><i></i>${ST[o.st][1]}</span></div><small style="color:var(--ink-3)">${o.ago} · ${o.pay}</small></div>
        <div class="menu-wrap">${nw(11, 'top:-8px;left:-10px')}<button class="icon-btn" data-a="omenu" aria-label="Ще">${ic('more')}</button>
          ${A.oMenu ? `<div class="menu"><button data-a="toast" data-v="Статус: Під питанням">${ic('msg')}Позначити «під питанням»</button><button data-a="toast" data-v="Замовлення скасовано, клієнту надіслано повідомлення">${ic('close')}Скасувати</button><hr><button class="danger" data-a="toast" data-v="Замовлення №${o.n} видалено">${ic('trash')}Видалити</button></div>` : ''}</div>
      </div>
      <div style="position:relative">${nw(5, 'top:-10px;right:-6px')}
        <div class="stepper-st">${FLOW.map((s, i) => `<button data-a="ostep" data-v="${s}" class="${i < idx ? 'on' : ''}${i === idx ? ' cur on' : ''}"><i></i>${FLOW_LABEL[s]}</button>`).join('')}</div>
        ${idx < FLOW.length - 1 ? `<div style="display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap"><button class="btn sm primary" data-a="ostep" data-v="${FLOW[Math.max(0, idx + 1)]}">${ic('check')}${o.st === 'new' || o.st === 'ask' ? 'Підтвердити' : 'Далі: ' + FLOW_LABEL[FLOW[idx + 1]]}</button><span style="font-size:12px;color:var(--ink-3);display:inline-flex;gap:5px;align-items:center">${ic('bell')}Клієнт отримає повідомлення в бот</span></div>` : ''}
      </div>
    </div>
    <div class="panel" style="display:grid;gap:10px">
      <div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><div><b>${esc(o.who)}</b> <span style="color:var(--ink-3)">${o.user}</span><div style="font-size:12.5px;color:var(--ink-2)">${o.phone}</div></div><button class="btn sm soft">${ic('send')}Написати</button></div>
      <div class="alert-row info" style="position:relative">${nw(6, 'top:-8px;right:-6px')}${ic('truck')}<span>${o.city}</span></div>
      ${['ready', 'ship'].includes(o.st) || o.ttn ? `<div class="f"><label for="ttn">ТТН Нової Пошти</label><input class="a-input" id="ttn" value="${o.ttn || ''}" placeholder="20450000000000"></div>` : ''}
      ${o.comment ? `<div style="font-size:13px;background:var(--surface-2);border-radius:10px;padding:8px 10px"><b>Коментар клієнта:</b> ${esc(o.comment)}</div>` : ''}
    </div>
    <div class="panel o-items" style="position:relative">${nw(7, 'top:-8px;right:-6px')}
      ${o.items.map(([id, q, c, pr]) => `<div class="oi"><img src="${img(PROD[id])}" alt=""><div><b>${esc(PROD[id].name)}</b>${q > 1 ? ` <span style="color:var(--ink-3)">×${q}</span>` : ''}<small>${c ? `<span class="dot-c" style="background:${filHex(c)}"></span>${filName(c)}` : 'конфігурація в коментарі'}</small></div>${o.contract ? `<input class="price-edit" placeholder="ціна" aria-label="Ціна позиції">` : `<b class="tnum">${money(pr * q)}</b>`}</div>`).join('')}
      <div class="sum" style="margin-top:8px">${o.promo ? `<div class="disc"><span>Акція −10%</span><span>−${money(o.promo)}</span></div>` : ''}<div class="total"><span>Разом</span><span>${o.contract ? '—' : money(sum)}</span></div></div>
      ${o.contract ? `<button class="btn sm primary" style="margin-top:8px" data-a="toast" data-v="Ціну збережено, клієнту надіслано суму">Зберегти ціну і повідомити клієнта</button>` : ''}
    </div>
    ${!o.contract ? `<div class="panel" style="position:relative">${nw(8, 'top:-8px;right:-6px')}<div class="profit"><div><small>Пластик</small><b>${grams} г</b></div><div><small>Собівартість</small><b>${money(cost)}</b></div><div><small>Прибуток</small><b class="up">${money(sum - cost)}</b></div></div></div>` : ''}
    <div class="panel" style="display:grid;gap:10px;position:relative">${nw(9, 'top:-8px;right:-6px')}
      <b style="font-size:13px">Шаблони повідомлень</b>
      <div class="tpl-chips">${tpls.map(([id, l]) => `<button class="tab-pill" data-a="tpl" data-v="${id}" aria-pressed="${A.tplSel === id}">${l}</button>`).join('')}</div>
      ${tpl ? `<div class="msg-preview">${esc(tpl[2])}</div><button class="btn sm primary" data-a="toast" data-v="Повідомлення надіслано ${o.user}">${ic('send')}Надіслати в бот</button>` : ''}
    </div>
    <div class="panel" style="position:relative">${nw(10, 'top:-8px;right:-6px')}<div class="f"><label for="onote">Нотатка для себе</label><textarea class="a-input" id="onote" rows="2" placeholder="Клієнт не бачить">${esc(o.note || '')}</textarea></div></div>
  </div>`;
}

// ── Черга друку ──
function queueRows() {
  const rows = [];
  ORDERS.filter(o => ['conf', 'print'].includes(o.st)).forEach(o => o.items.forEach(([id, q, c]) => { if (c) rows.push({ key: `${o.n}-${id}`, id, q, c, n: o.n }); }));
  return rows;
}
function pgQueue() {
  const rows = queueRows();
  const byColor = {};
  rows.forEach(r => (byColor[r.c] = byColor[r.c] || []).push(r));
  const gramsOf = r => r.q * (r.id === 145 ? 210 : 25);
  const minsOf = r => r.q * (r.id === 145 ? 450 : 110);
  const left = rows.filter(r => !A.queueDone.has(r.key));
  const tg = left.reduce((s, r) => s + gramsOf(r), 0), tm = left.reduce((s, r) => s + minsOf(r), 0);
  return `<div class="kpis" style="grid-template-columns:repeat(3,minmax(0,1fr))">
      <div class="kpi"><span class="l">${ic('cube')}Деталей до друку ${nw(1)}</span><span class="v">${left.reduce((s, r) => s + r.q, 0)}</span></div>
      <div class="kpi"><span class="l">${ic('clock')}Час друку ${nw(3)}</span><span class="v">${Math.floor(tm / 60)} год ${tm % 60} хв</span></div>
      <div class="kpi"><span class="l">${ic('spool')}Пластику</span><span class="v">${tg} г</span></div>
    </div>
    ${Object.entries(byColor).map(([c, rs], gi) => { const need = rs.filter(r => !A.queueDone.has(r.key)).reduce((s, r) => s + gramsOf(r), 0); return `<div class="queue-group">
      <div class="queue-head">${gi === 0 ? nw(2) : ''}<span class="spool" style="background:${filHex(c)}"></span><div><b>${filName(c)}</b><br><small>${rs.length} позиц. · ~${need} г пластику</small></div></div>
      ${rs.map((r, ri) => `<label class="q-row${A.queueDone.has(r.key) ? ' done' : ''}"><input type="checkbox" data-a="qdone" data-v="${r.key}" ${A.queueDone.has(r.key) ? 'checked' : ''}><img src="${img(PROD[r.id])}" alt=""><div><b>${esc(PROD[r.id].name)}</b>${gi === 0 && ri === 0 ? ' ' + nw(4) : ''}<br><small>${gramsOf(r)} г · ${Math.floor(minsOf(r) / 60)} год ${minsOf(r) % 60} хв</small></div><span class="qx">×${r.q}</span><div class="ord-tags"><span>№${r.n}</span></div><span style="font-size:12px;color:var(--ink-3)">${A.queueDone.has(r.key) ? 'надруковано' : 'в черзі'}</span></label>`).join('')}
    </div>`; }).join('')}`;
}

// ── Товари ──
function pgProducts() {
  const tabs = [['active', 'Активні'], ['hidden', 'Приховані'], ['archive', 'Архів']];
  const cnt = t => D.products.filter(p => A.pstatus[p.id] === t).length;
  const q = A.prodQ.trim().toLowerCase();
  const onSale = p => !p.contract && p.oldPrice && A.prices[p.id] < p.oldPrice;
  const list = D.products.filter(p => A.pstatus[p.id] === A.prodTab && (!q || p.name.toLowerCase().includes(q)) && (!A.prodSale || onSale(p)));
  const allSel = list.length && list.every(p => A.prodSel.has(p.id));
  return `<div class="toolbar" style="justify-content:space-between">
    <div class="tabs-line">${nw(1)}${tabs.map(([id, l]) => `<button class="tab-pill" data-a="ptab" data-v="${id}" aria-pressed="${A.prodTab === id}">${l}<span class="c">${cnt(id)}</span></button>`).join('')}
      <span style="width:1px;background:var(--line-2);margin:4px 4px"></span>
      <button class="tab-pill" data-a="psale" aria-pressed="${A.prodSale}">${ic('percent').replace('class="i ', 'style="width:14px;height:14px" class="i ')}Акційні<span class="c">${D.products.filter(p => A.pstatus[p.id] === A.prodTab && onSale(p)).length}</span></button>${nw(10)}</div>
    <div class="toolbar"><input class="a-input" id="prod-q" placeholder="Пошук товару" value="${esc(A.prodQ)}" style="width:200px" aria-label="Пошук товару">
      <select class="a-input" aria-label="Категорія"><option>Усі категорії</option>${D.categories.map(c => `<option>${c.emoji} ${esc(c.name)}</option>`).join('')}</select></div>
  </div>
  <div class="table-wrap" style="position:relative">
    <table class="t">
      <thead><tr><th style="width:36px"><input type="checkbox" data-a="selall" ${allSel ? 'checked' : ''} aria-label="Вибрати всі"></th><th>Товар ${nw(2)}</th><th>Ціна ${nw(3)}</th><th>Стара</th><th>Позначки ${nw(4)}</th><th>Перегляди / кошик / продажі ${nw(5)}</th><th>Статус</th><th style="width:44px">${nw(6)}</th></tr></thead>
      <tbody>${list.map(p => { const s = statFor(p.id); const f = A.flags[p.id]; const d = !p.contract && p.oldPrice ? Math.round((1 - A.prices[p.id] / p.oldPrice) * 100) : 0; return `<tr class="click${A.prodSel.has(p.id) ? ' sel' : ''}" data-a="edit" data-v="${p.id}">
        <td data-stop><input type="checkbox" data-a="psel" data-v="${p.id}" ${A.prodSel.has(p.id) ? 'checked' : ''} aria-label="Вибрати"></td>
        <td><div class="p-cell"><img src="${img(p)}" alt=""><div><b>${esc(p.name)}</b><small>${CAT[p.cat]?.emoji || ''} ${esc(CAT[p.cat]?.name || '')} · ID ${p.id}</small></div></div></td>
        <td data-stop>${p.contract ? '<span class="contract-tag">договірна</span>' : `<input class="price-edit" value="${A.prices[p.id]}" data-a="price" data-v="${p.id}" aria-label="Ціна">`}</td>
        <td>${p.oldPrice ? `<span class="price-old">${p.oldPrice}</span> <span class="b-pill sale">−${d}%</span>` : '<span style="color:var(--ink-3)">—</span>'}</td>
        <td data-stop><div class="flag-set">
          <button class="flag f-hot" aria-pressed="${f.hot}" data-a="flag" data-v="${p.id}" data-f="hot" title="Хіт">${ic('flame')}</button>
          <button class="flag f-pin" aria-pressed="${f.pin}" data-a="flag" data-v="${p.id}" data-f="pin" title="Закріплений на головній">${ic('pin')}</button>
          <button class="flag f-gift" aria-pressed="${f.gift}" data-a="flag" data-v="${p.id}" data-f="gift" title="Може бути подарунком">${ic('gift')}</button></div></td>
        <td><span class="stats-mini"><span>${ic('eye')}${s.v}</span><span>${ic('cart')}${s.c}</span><span>${ic('check')}${s.s}</span></span></td>
        <td><span class="status-dot"><i style="background:${A.pstatus[p.id] === 'active' ? 'var(--green)' : A.pstatus[p.id] === 'hidden' ? 'var(--amber)' : 'var(--ink-3)'}"></i>${A.pstatus[p.id] === 'active' ? 'В магазині' : A.pstatus[p.id] === 'hidden' ? 'Прихований' : 'В архіві'}</span></td>
        <td data-stop><div class="menu-wrap"><button class="icon-btn" style="width:32px;height:32px" data-a="pmenu" data-v="${p.id}" aria-label="Дії">${ic('more')}</button>
          ${A.prodMenu === p.id ? `<div class="menu"><button data-a="edit" data-v="${p.id}">${ic('edit')}Редагувати</button><button data-a="dup" data-v="${p.id}">${ic('copy')}Дублювати</button><button data-a="toast" data-v="Відкрито в магазині">${ic('eye')}Подивитись у магазині</button><hr>
          ${A.pstatus[p.id] === 'active' ? `<button data-a="pst" data-v="${p.id}" data-s="hidden">${ic('eyeoff')}Приховати</button><button data-a="pst" data-v="${p.id}" data-s="archive">${ic('archive')}В архів</button>` : `<button data-a="pst" data-v="${p.id}" data-s="active">${ic('refresh')}Повернути в магазин</button>`}</div>` : ''}</div></td>
      </tr>`; }).join('')}</tbody>
    </table>
  </div>
  ${A.prodSel.size ? `<div class="bulk">${nw(7)}<b>Вибрано: ${A.prodSel.size}</b><span style="opacity:.6">|</span>
    <span>Знижка</span><input class="a-input" id="bulk-pct" value="${A.bulkPct}" aria-label="Знижка у відсотках"><span>%</span><button class="btn sm primary" data-a="bulkdisc">Застосувати</button>
    <button class="btn sm" data-a="toast" data-v="Категорію змінено">${ic('folder')}Категорія</button>
    <button class="btn sm" data-a="bulkhide">${ic('eyeoff')}Приховати</button>
    <button class="btn sm" style="margin-left:auto" data-a="bulkclear">${ic('close')}Скасувати</button></div>` : `<p style="margin:0;color:var(--ink-3);font-size:12.5px">${nw(8)} Натисни на рядок, щоб відкрити редактор. Постав галочки — з'являться масові дії.</p>`}`;
}

function editorHtml(id) {
  const isNew = id === 'new';
  const p = isNew ? { id: 0, name: '', cat: 'key', price: 100, oldPrice: null, imgs: [], mat: '' , contract: false, filament: true } : PROD[id];
  const det = detailsFor(p);
  const price = isNew ? 100 : A.prices[p.id];
  const d = p.oldPrice && price < p.oldPrice ? Math.round((1 - price / p.oldPrice) * 100) : 0;
  const notes = ['Секції замість однієї довгої форми', 'Знижка рахується автоматично', 'Опис — необовʼязковий, можна на кілька абзаців', 'Примітка — помітна плашка в картці, теж необовʼязкова', 'Фото: обкладинка, порядок, відео', 'Емодзі необовʼязковий, коли є фото', 'STL — лише для тебе, покупці не бачать', 'Попередній перегляд картки як у магазині', 'Незбережені зміни і закріплена кнопка «Зберегти»'];
  return `<div class="drawer-back" data-a="close-drawer-back"><div class="drawer" role="dialog" aria-label="Редактор товару">
    <div class="drawer-head"><button class="icon-btn" data-a="close-drawer" aria-label="Закрити">${ic('close')}</button><h2>${isNew ? 'Новий товар' : esc(p.name)}</h2>${!isNew ? `<span class="st ready" style="margin-left:4px"><i></i>В магазині</span>` : ''}<span style="flex:1"></span><button class="btn sm ghost" data-a="toast" data-v="Копію створено">${ic('copy')}Дублювати</button></div>
    <div class="drawer-body">
      <div class="adm-notes" style="grid-column:1/-1;margin:0"><span class="lbl">Що нового</span><ol>${notes.map((t, i) => `<li><span class="nw">${i + 1}</span>${t}</li>`).join('')}</ol></div>
      <div style="display:grid;gap:14px;min-width:0">
        <section class="form-sec"><h4>Основне ${nw(1)}</h4>
          <div class="f"><label for="e-name">Назва</label><input class="a-input" id="e-name" value="${esc(p.name)}" data-a="dirty" placeholder="Напр. Брелок-зірка"></div>
          <div class="f-grid"><div class="f"><label for="e-cat">Категорія</label><select class="a-input" id="e-cat" data-a="dirty">${D.categories.map(c => `<option ${c.id === p.cat ? 'selected' : ''}>${c.emoji} ${esc(c.name)}</option>`).join('')}</select></div>
          <div class="f"><label for="e-emoji">Емодзі ${nw(6)}</label><input class="a-input" id="e-emoji" placeholder="Необовʼязково — є фото"><span class="hint">Потрібен лише для товарів без фото</span></div></div>
        </section>
        <section class="form-sec"><h4>Ціна ${nw(2)}</h4>
          <label class="toggle-row" style="padding-top:0"><span>Договірна ціна<small>Покупець бачить «ціна після узгодження»</small></span><span class="sw"><input type="checkbox" ${p.contract ? 'checked' : ''}><span></span></span></label>
          <div class="f-grid"><div class="f"><label for="e-price">Ціна, ₴</label><input class="a-input" id="e-price" type="number" value="${price}" data-a="eprice"></div>
          <div class="f"><label for="e-old">Стара ціна, ₴</label><input class="a-input" id="e-old" type="number" value="${p.oldPrice || ''}" placeholder="для знижки" data-a="dirty"></div></div>
          <div id="auto-disc">${d ? `<span class="auto-disc">Знижка −${d}% · покупець економить ${money(p.oldPrice - price)}</span>` : `<span class="hint" style="font-size:12px;color:var(--ink-3)">Вкажи стару ціну — знижка у відсотках порахується сама</span>`}</div>
        </section>
        <section class="form-sec"><h4>Опис ${nw(3)}</h4>
          <div class="f"><label for="e-short">Короткий рядок під назвою</label><input class="a-input" id="e-short" value="${esc(p.mat)}"></div>
          <div class="f"><label for="e-desc">Опис</label><textarea class="a-input" id="e-desc" rows="4" data-a="dirty" placeholder="Необовʼязково">${esc(isNew ? '' : det.desc)}</textarea><span class="hint">Необовʼязково. Порожнє поле — блоку опису в картці немає</span></div>
          <div class="f"><label for="e-note">Примітка ${nw(4)}</label><input class="a-input" id="e-note" data-a="dirty" value="${esc(isNew ? '' : det.note)}" placeholder="Напр. «Дрібні деталі — не давай дітям до 3 років»"><span class="hint">Необовʼязково. Показується помітною плашкою з іконкою ⓘ</span></div>
        </section>
        <section class="form-sec"><h4>Фото і відео ${nw(5)}</h4>
          <div class="photo-grid">${p.imgs.map((s, i) => `<div class="ph-tile"><img src="${s}" alt="">${i === 0 ? '<span class="cover">Обкладинка</span>' : ''}<span class="grip">${ic('grip')}</span></div>`).join('')}
            <button class="ph-tile ph-add">${ic('image')}Додати фото</button><button class="ph-tile ph-add">${ic('play')}Додати відео</button></div>
          <span class="hint" style="font-size:12px;color:var(--ink-3)">Перетягни, щоб змінити порядок. Перше фото — обкладинка. GIF автоматично стає легким відео.</span>
        </section>
        <section class="form-sec"><h4>Опції</h4>
          <label class="toggle-row"><span>Вибір кольору PLA<small>Покупець обирає колір у картці</small></span><span class="sw"><input type="checkbox" ${p.filament ? 'checked' : ''}><span></span></span></label>
          <label class="toggle-row"><span>Кольори, що світяться</span><span class="sw"><input type="checkbox"><span></span></span></label>
          <label class="toggle-row"><span>Хіт</span><span class="sw"><input type="checkbox" ${A.flags[p.id]?.hot ? 'checked' : ''}><span></span></span></label>
          <label class="toggle-row"><span>Закріплений на головній</span><span class="sw"><input type="checkbox" ${A.flags[p.id]?.pin ? 'checked' : ''}><span></span></span></label>
          <label class="toggle-row"><span>Може бути подарунком<small>Для акції «подарунок за 3 брелки», до 100 ₴</small></span><span class="sw"><input type="checkbox" ${A.flags[p.id]?.gift ? 'checked' : ''}><span></span></span></label>
        </section>
        <section class="form-sec"><h4>Внутрішнє ${nw(7)}</h4>
          <div class="f"><label for="e-stl">Посилання на STL-модель</label><input class="a-input" id="e-stl" value="${isNew ? '' : 'https://makerworld.com/uk/models/…'}"><span class="hint">${ic('eyeoff').replace('class="i ', 'style="width:12px;height:12px;vertical-align:-2px" class="i ')} Видно лише тобі — у публічному API його більше немає</span></div>
        </section>
      </div>
      <div class="preview-col"><span class="lbl">Так виглядає в магазині ${nw(8)}</span>
        <div class="preview-phone">${isNew ? `<div class="card"><div class="thumb" style="display:grid;place-items:center;color:var(--ink-3)">${ic('image')}</div><div class="card-info"><div class="card-name">Назва товару</div><div class="card-foot"><span class="price">100 ₴</span><span class="add-btn">${ic('plus')}</span></div></div></div>` : card({ ...p, price })}</div>
        <p style="margin:0;font-size:12px;color:var(--ink-3)">Превʼю оновлюється, поки редагуєш.</p>
      </div>
    </div>
    <div class="drawer-foot">${A.dirty ? `<span class="unsaved"><i></i>Є незбережені зміни ${nw(9)}</span>` : `<span style="font-size:12.5px;color:var(--ink-3)">Змін немає ${nw(9)}</span>`}<span style="flex:1"></span><button class="btn ghost" data-a="close-drawer">Скасувати</button><button class="btn primary" data-a="save">${ic('check')}Зберегти</button></div>
  </div></div>`;
}

// ── Шухляда для форм «Додати …» ──
function drawerShell({ title, badge = '', notes = [], body, preview = '', narrow = false, saveLabel = 'Зберегти', extraHead = '' }) {
  return `<div class="drawer-back" data-a="close-drawer-back"><div class="drawer${narrow ? ' narrow' : ''}" role="dialog" aria-label="${esc(title)}">
    <div class="drawer-head"><button class="icon-btn" data-a="close-drawer" aria-label="Закрити">${ic('close')}</button><h2>${esc(title)}</h2>${badge}<span style="flex:1"></span>${extraHead}</div>
    <div class="drawer-body">
      ${notes.length ? `<div class="adm-notes" style="grid-column:1/-1;margin:0"><span class="lbl">Що нового</span><ol>${notes.map((t, i) => `<li><span class="nw">${i + 1}</span>${t}</li>`).join('')}</ol></div>` : ''}
      <div style="display:grid;gap:14px;min-width:0;align-content:start">${body}</div>
      ${preview ? `<div class="preview-col">${preview}</div>` : ''}
    </div>
    <div class="drawer-foot">${A.dirty ? '<span class="unsaved"><i></i>Є незбережені зміни</span>' : '<span style="font-size:12.5px;color:var(--ink-3)">Змін немає</span>'}<span style="flex:1"></span><button class="btn ghost" data-a="close-drawer">Скасувати</button><button class="btn primary" data-a="save">${ic('check')}${saveLabel}</button></div>
  </div></div>`;
}
const sw = (name, on, extra = '') => `<span class="sw"><input type="checkbox" data-f="${name}" ${on ? 'checked' : ''} ${extra}><span></span></span>`;
const fld = (label, inner, hint = '') => `<div class="f"><label>${label}</label>${inner}${hint ? `<span class="hint">${hint}</span>` : ''}</div>`;

// Акція
function promoForm() {
  const p = A.pf;
  const t = p.type;
  const isNew = A.drawer.id == null;
  const showVal = ['order_percent', 'order_fixed', 'cat_percent'].includes(t);
  const showMin = ['order_percent', 'order_fixed', 'free_ship'].includes(t);
  const showCat = ['gift_threshold', 'cat_percent'].includes(t);
  const body = `
    ${isNew ? `<section class="form-sec"><h4>Готовий шаблон ${nw(1)}</h4>
      ${PRESETS.map(([g, items]) => `<div class="preset-group">${g}</div><div class="preset-grid">${items.map(([id, l, d]) => `<button type="button" class="preset" data-a="preset" data-v="${id}" aria-pressed="${p.preset === id}"><span class="em">${d.emoji}</span><span>${l}<small>${PROMO_TYPES.find(x => x[0] === d.type)[2]}</small></span></button>`).join('')}</div>`).join('')}
      <span class="hint" style="font-size:12px;color:var(--ink-3)">Заповнить поля за секунду, далі можна змінити будь-що.</span></section>` : ''}
    <section class="form-sec"><h4>Тип акції ${nw(2)}</h4>
      <div class="type-grid">${PROMO_TYPES.map(([id, em, l, d, isNewType]) => `<button type="button" class="type-card" data-a="ptype" data-v="${id}" aria-pressed="${t === id}">${isNewType ? '<span class="tag-new">нове</span>' : ''}<span class="em">${em}</span><b>${l}</b><small>${d}</small></button>`).join('')}</div>
    </section>
    <section class="form-sec"><h4>Картка в магазині</h4>
      ${fld('Назва', `<input class="a-input" data-f="title" value="${esc(p.title)}" placeholder="напр.: −15% на зимову колекцію">`)}
      ${fld('Опис', `<input class="a-input" data-f="subtitle" value="${esc(p.subtitle)}" placeholder="Коротко про умови акції">`)}
      <div class="f-grid">${fld('Emoji', `<input class="a-input" data-f="emoji" value="${esc(p.emoji)}" maxlength="8">`)}${fld('Бейдж', `<input class="a-input" data-f="badge" value="${esc(p.badge)}" placeholder="−10%">`, 'необовʼязково')}</div>
      ${fld(`Колір акценту ${nw(6)}`, `<div class="acc-row" role="group" aria-label="Колір акценту">
        <button type="button" class="acc-auto" data-a="pacc" data-v="" aria-pressed="${!p.accent}">Авто</button>
        ${ACCENTS.map(([id, l]) => `<button type="button" class="acc-sw ac-${id}" data-a="pacc" data-v="${id}" aria-pressed="${p.accent === id}" title="${l}" aria-label="${l}">${ic('check')}</button>`).join('')}</div>`,
        'Авто — колір залежить від типу акції. Вибраний колір бачать банер і прогрес у кошику')}
    </section>
    <section class="form-sec"><h4>Умови ${nw(3)}</h4>
      ${showVal || showMin ? `<div class="f-grid">
        ${showVal ? fld(t === 'order_fixed' ? 'Розмір знижки, ₴' : 'Розмір знижки, %', `<input class="a-input" type="number" data-f="value" value="${p.value}" min="0">`) : ''}
        ${showMin ? fld('Мінімальна сума, ₴', `<input class="a-input" type="number" data-f="min_order" value="${p.min_order}" min="0">`, '0 — будь-яка сума') : ''}</div>` : ''}
      ${showCat ? fld(t === 'gift_threshold' ? 'Категорія товарів для подарунка' : 'Категорія зі знижкою', `<select class="a-input" data-f="category"><option value="">Будь-яка категорія</option>${D.categories.map(c => `<option value="${c.id}" ${p.category === c.id ? 'selected' : ''}>${c.emoji} ${esc(c.name)}</option>`).join('')}</select>`) : ''}
      ${t === 'gift_threshold' ? `<div class="f-grid">${fld('Скільки товарів потрібно', `<input class="a-input" type="number" data-f="min_items" value="${p.min_items}" min="1">`)}${fld('Макс. ціна подарунка, ₴', `<input class="a-input" type="number" data-f="max_gift_price" value="${p.max_gift_price}" min="0">`, 'Подарунком стає товар з позначкою 🎁')}</div>` : ''}
      ${t === 'info' ? `<span class="hint" style="font-size:12.5px;color:var(--ink-2)">Банер без автоматичної знижки: інформує про доставку, бонус за відгук тощо. Умову виконуєш вручну.</span>` : ''}
      <div id="pf-cond" class="auto-disc" style="justify-self:start">${promoCond(p)}</div>
    </section>
    <section class="form-sec"><h4>Розклад ${nw(5)}</h4>
      <div class="f-grid">${fld('Старт', `<input class="a-input" type="date" data-f="starts" value="${p.starts || ''}">`, 'порожньо — одразу')}${fld('Завершення', `<input class="a-input" type="date" data-f="ends" value="${p.ends || ''}">`, 'порожньо — без кінця')}</div>
      <label class="toggle-row"><span>Таймер до кінця в магазині<small>Лише якщо є дата завершення</small></span>${sw('timer', p.timer)}</label>
      <div class="f-grid">${fld('Порядок показу', `<input class="a-input" type="number" data-f="priority" value="${p.priority}" min="0">`, 'менше число — вище')}<div></div></div>
      <label class="toggle-row"><span>Активна</span>${sw('active', p.active)}</label>
    </section>`;
  const preview = `<span class="lbl">Так виглядає в магазині ${nw(4)}</span>
    <div class="preview-phone" id="pf-preview">${promoHero(p)}</div>
    <p style="margin:0;font-size:12px;color:var(--ink-3)">Банер на головній, картка у вкладці «Акції» і рядок у кошику з прогресом.</p>`;
  return drawerShell({
    title: isNew ? 'Нова акція' : p.title || 'Акція', body, preview,
    badge: isNew ? '' : `<span class="st ${PROMO_ST[promoStatus(p)][0]}" style="margin-left:4px"><i></i>${PROMO_ST[promoStatus(p)][1]}</span>`,
    notes: isNew ? ['16 шаблонів з твоєї адмінки, згруповані', 'Тип акції картками замість списку; 2 нові типи', 'Лише поля, потрібні для обраного типу', 'Живе превʼю банера', 'Розклад і таймер', 'Колір акценту: банер і прогрес у кошику міняють колір одразу'] : [],
    saveLabel: isNew ? 'Створити акцію' : 'Зберегти',
  });
}
// Купон
function couponForm() {
  const c = A.cf;
  const isNew = A.drawer.id == null;
  const body = `
    <section class="form-sec"><h4>Код і знижка ${nw(1)}</h4>
      ${fld('Код купона', `<div style="display:flex;gap:6px"><input class="a-input code" data-f="code" value="${esc(c.code)}" style="text-transform:uppercase"><button type="button" class="btn sm ghost" data-a="gen">${ic('refresh')}Згенерувати</button></div>`)}
      <div class="f-grid">${fld('Тип знижки', `<div class="seg" role="group"><button type="button" data-a="ctype" data-v="percent" aria-pressed="${c.type === 'percent'}">Відсоток, %</button><button type="button" data-a="ctype" data-v="fixed" aria-pressed="${c.type === 'fixed'}">Сума, ₴</button></div>`)}
        ${fld(c.type === 'percent' ? 'Значення, %' : 'Значення, ₴', `<input class="a-input" type="number" data-f="value" value="${c.value}" min="1">`)}</div>
    </section>
    <section class="form-sec"><h4>Обмеження ${nw(2)}</h4>
      <div class="f-grid">${fld('Мін. сума замовлення, ₴', `<input class="a-input" type="number" data-f="min" value="${c.min}" min="0">`)}${fld('Макс. використань', `<input class="a-input" type="number" data-f="max" value="${c.max}" min="0">`, '0 — безліміт')}</div>
      <div class="f-grid">${fld('Діє до', `<input class="a-input" type="date" data-f="exp" value="${c.exp}">`, 'порожньо — безстроково')}<div></div></div>
      <label class="toggle-row"><span>Одноразовий<small>Один клієнт — одне використання</small></span>${sw('onePer', c.onePer)}</label>
      <label class="toggle-row"><span>Сумувати з акційною знижкою<small>Інакше промокод замінює автоматичну знижку</small></span>${sw('stack', c.stack)}</label>
    </section>
    <section class="form-sec"><h4>Хто може застосувати ${nw(3)}</h4>
      <label class="toggle-row" style="padding-top:0"><span>📢 Лише для підписників каналу<small>Бот перевіряє підписку при застосуванні</small></span>${sw('sub', c.sub)}</label>
      ${c.sub ? fld('Канал або група', `<input class="a-input" data-f="channel" value="${esc(c.channel)}" placeholder="@channel або -1001589686948">`, 'Порожньо — канал за замовчуванням (SUBSCRIPTION_CHANNEL)') : ''}
      ${fld('Лише для цих Telegram ID', `<div class="id-chips">${c.ids.map((id, i) => `<span>${esc(id)}<button type="button" data-a="cid-rm" data-v="${i}" aria-label="Прибрати">${ic('close')}</button></span>`).join('')}<input id="cid-in" placeholder="${c.ids.length ? 'Ще ID + Enter' : 'ID + Enter, або вибери клієнта'}" inputmode="numeric"></div>`, 'Порожньо — публічний купон')}
      <div style="display:flex;gap:6px;flex-wrap:wrap">${[['Олена К.', '512804337'], ['Тарас Б.', '380116642'], ['Ігор П.', '644201958']].map(([n, id]) => `<button type="button" class="tab-pill" data-a="cid-add" data-v="${id}">${ic('plus').replace('class="i ', 'style="width:12px;height:12px" class="i ')}${n}</button>`).join('')}</div>
    </section>
    <label class="toggle-row form-sec" style="padding:12px 16px"><span><b>Активний</b></span>${sw('active', c.active)}</label>`;
  const preview = `<span class="lbl">Так бачить клієнт ${nw(4)}</span>
    <div class="preview-phone" id="cf-preview">${couponPreview(c)}</div>`;
  return drawerShell({
    title: isNew ? 'Новий купон' : c.code, body, preview,
    notes: isNew ? ['Код з генератором', 'Усі обмеження з теперішньої адмінки в одній формі', 'Підписники каналу і доступ за Telegram ID', '«Мої купони» і посилання для поста'] : [],
    saveLabel: isNew ? 'Створити купон' : 'Зберегти',
  });
}
function couponPreview(c) {
  const cond = [c.min ? `від ${money(c.min)}` : 'на будь-яку суму', c.exp ? `до ${dateUa(c.exp)}` : '', c.sub ? 'для підписників каналу' : ''].filter(Boolean).join(' · ');
  return `<div style="font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);margin-bottom:8px">Мої купони</div>
    <div class="mc-preview"><span class="tk">${couponValue(c)}</span><div><b class="code">${esc(c.code || 'КОД')}</b><div style="font-size:12px;color:var(--ink-2)">${cond}</div></div></div>
    <div class="link-box" style="margin-top:10px">${ic('link')}<span>t.me/poselyanov3dprint_bot?startapp=coupon_${esc(c.code || 'КОД')}</span></div>
    <p style="margin:8px 0 0;font-size:12px;color:var(--ink-3)">Посилання відкриває магазин і саме застосовує код у кошику.</p>`;
}
// Категорія
const CAT_EMOJI = ['🧸', '🧠', '🏠', '📲', '🐟', '🔑', '🪴', '⚒️', '✏️', '🎁', '🎄', '⭐', '🚗', '📚', '✂️', '🎨', '🎮', '🐾'];
function categoryForm() {
  const f = A.catf;
  const body = `
    <section class="form-sec"><h4>Категорія</h4>
      ${fld('Назва', `<input class="a-input" data-f="name" value="${esc(f.name)}" placeholder="напр.: Канцелярія">`)}
      ${fld('ID (генерується з назви)', `<div class="slug" id="catf-slug" style="font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;color:var(--ink-2);padding:6px 0">${slugify(f.name) || '—'}</div>`)}
      ${fld('Емодзі', `<div class="emoji-row">${CAT_EMOJI.map(e => `<button type="button" data-a="cat-emoji" data-v="${e}" aria-pressed="${f.emoji === e}">${e}</button>`).join('')}</div>`)}
    </section>
    <section class="form-sec"><h4>Показ у магазині</h4>
      ${fld('Слот на головній', `<select class="a-input" data-f="slot">${['— не показувати в рядку', 'Слот 1', 'Слот 2', 'Слот 3', 'Слот 4'].map((s, k) => `<option value="${k}" ${f.slot === k ? 'selected' : ''}>${s}</option>`).join('')}</select>`, 'Позиція категорії в рядку на головній')}
      <label class="toggle-row"><span>Показувати в магазині</span>${sw('active', f.active)}</label>
    </section>`;
  const preview = `<span class="lbl">Так виглядає в магазині</span><div class="preview-phone" id="catf-preview">${catPreview(f)}</div>`;
  return drawerShell({ title: 'Нова категорія', body, preview, narrow: true, saveLabel: 'Додати категорію' });
}
const catPreview = f => `<button class="cat-chip" type="button">${f.emoji} ${esc(f.name || 'Назва')} <small>0</small></button>`;
// Колір філаменту
function filamentForm() {
  const f = A.filf;
  const body = `
    <section class="form-sec"><h4>Колір</h4>
      ${fld('Назва', `<input class="a-input" data-f="name" value="${esc(f.name)}" placeholder="напр.: Мʼятний">`)}
      ${fld('Колір (HEX)', `<div class="color-pick"><input type="color" data-f="hex" value="${f.hex}" aria-label="Колір"><input class="a-input" data-f="hexText" value="${f.hex}" style="width:120px;font-family:ui-monospace,Menlo,monospace"></div>`)}
      <label class="toggle-row"><span>Світиться в темряві<small>У магазині кружечок має сяйво</small></span>${sw('glow', f.glow)}</label>
    </section>
    <section class="form-sec"><h4>Наявність</h4>
      <label class="toggle-row"><span>В наявності<small>Інакше колір не можна обрати в магазині</small></span>${sw('avail', f.avail)}</label>
    </section>`;
  const preview = `<span class="lbl">Так виглядає в картці товару</span><div class="preview-phone" id="filf-preview">${filPreview(f)}</div>`;
  return drawerShell({ title: 'Новий колір', body, preview, narrow: true, saveLabel: 'Додати колір' });
}
const filPreview = f => `<div class="swatches">${FIL.filter(x => x.available).slice(0, 4).map(x => `<span class="swatch" style="background:${x.hex}"></span>`).join('')}<span class="swatch${f.glow ? ' glow' : ''}" style="background:${f.hex}" aria-pressed="true"></span></div>
  <div class="swatch-name" style="margin-top:10px">Обрано: <b>${esc(f.name || 'Новий колір')}</b></div>`;
// Замовлення вручну
function orderForm() {
  const o = A.of;
  const body = `
    <section class="form-sec"><h4>Клієнт</h4>
      <div class="f-grid">${fld('Імʼя', `<input class="a-input" data-f="who" value="${esc(o.who)}" placeholder="Олена К.">`)}${fld('Telegram або телефон', `<input class="a-input" data-f="contact" value="${esc(o.contact)}" placeholder="@username або +380…">`, 'Якщо клієнт запускав бота — отримає статуси')}</div>
      ${fld('Звідки замовлення', `<div class="tabs-line">${['Instagram', 'Telegram (особисто)', 'OLX', 'Особисто'].map(s => `<button type="button" class="tab-pill" data-a="osrc" data-v="${s}" aria-pressed="${o.src === s}">${s}</button>`).join('')}</div>`)}
    </section>
    <section class="form-sec"><h4>Товари</h4>
      ${o.items.map(([id, q, c], i) => `<div class="f-grid" style="grid-template-columns:minmax(0,1fr) 130px 70px 32px;align-items:end">
        ${fld(i ? '' : 'Товар', `<select class="a-input" data-oi="${i}" data-k="id">${D.products.map(p => `<option value="${p.id}" ${p.id === id ? 'selected' : ''}>${esc(p.name)}${p.contract ? '' : ' · ' + money(p.price)}</option>`).join('')}</select>`)}
        ${fld(i ? '' : 'Колір', `<select class="a-input" data-oi="${i}" data-k="c">${FIL.filter(f => f.available).map(f => `<option value="${f.id}" ${f.id === c ? 'selected' : ''}>${f.name}</option>`).join('')}</select>`)}
        ${fld(i ? '' : 'К-сть', `<input class="a-input" type="number" min="1" value="${q}" data-oi="${i}" data-k="q">`)}
        <button type="button" class="icon-btn" style="width:32px;height:38px" data-a="oi-rm" data-v="${i}" aria-label="Прибрати">${ic('trash')}</button></div>`).join('')}
      <button type="button" class="btn sm ghost" style="justify-self:start" data-a="oi-add">${ic('plus')}Додати позицію</button>
      <div class="sum"><div class="total"><span>Разом</span><span id="of-total">${money(o.items.reduce((s, [id, q]) => s + (PROD[id].contract ? 0 : PROD[id].price * q), 0))}</span></div></div>
    </section>
    <section class="form-sec"><h4>Доставка і оплата</h4>
      <div class="f-grid">${fld('Місто або село', `<input class="a-input" data-f="city" value="${esc(o.city)}" placeholder="Київ">`)}${fld('Відділення', `<input class="a-input" data-f="branch" value="${esc(o.branch)}" placeholder="НП №12 або індекс Укрпошти">`)}</div>
      ${fld('Оплата', `<select class="a-input" data-f="pay"><option>Картка · оплачено</option><option>Картка після підтвердження</option><option>Накладений платіж</option><option>Готівка</option></select>`)}
      ${fld('Коментар', `<textarea class="a-input" data-f="comment" rows="2" placeholder="Кольори, написи…">${esc(o.comment)}</textarea>`)}
    </section>`;
  return drawerShell({ title: 'Нове замовлення вручну', body, narrow: true, saveLabel: 'Створити замовлення' });
}
function renderDrawerBody() {
  const k = A.drawer?.k;
  if (!k) return '';
  if (k === 'product') return editorHtml(A.drawer.id == null ? 'new' : A.drawer.id);
  return { promo: promoForm, coupon: couponForm, category: categoryForm, filament: filamentForm, order: orderForm }[k]();
}
function openDrawer(k, id = null) {
  A.drawer = { k, id }; A.dirty = false; A.prodMenu = null;
  if (k === 'promo') A.pf = id == null ? { ...PROMO_BLANK, preset: '' } : { ...A.promos[id] };
  if (k === 'coupon') A.cf = id == null ? { code: 'OSIN' + (30 + Math.floor(Math.random() * 20)), type: 'fixed', value: 30, min: 200, max: 0, exp: '', onePer: true, stack: false, sub: false, channel: '', ids: [], active: true, used: 0, rev: 0 } : { ...A.coupons[id], ids: [...A.coupons[id].ids] };
  if (k === 'category') A.catf = { name: 'Канцелярія', emoji: '✏️', slot: 0, active: true };
  if (k === 'filament') A.filf = { name: 'Мʼятний', hex: '#7fd8be', glow: false, avail: true };
  if (k === 'order') A.of = { who: '', contact: '', src: 'Instagram', items: [[8, 1, 'black']], city: '', branch: '', comment: '' };
  renderDrawer();
}
// Зчитати поля форми в стан (без повного перерендеру — щоб не губився фокус)
function readForm(target) {
  const root = document.getElementById('drawer-root');
  root.querySelectorAll('[data-f]').forEach(el => {
    const k = el.dataset.f; let v = el.type === 'checkbox' ? el.checked : el.type === 'number' ? Number(el.value) || 0 : el.value;
    if (k === 'hexText') { if (/^#[0-9a-f]{6}$/i.test(v)) target.hex = v; return; }
    if (k === 'code') v = v.toUpperCase();
    if (k === 'slot') v = Number(v);
    target[k] = v;
  });
}

// ── Категорії, філаменти ──
const slugify = s => s.toLowerCase().replace(/і/g, 'i').replace(/ї/g, 'yi').replace(/є/g, 'ie').replace(/ґ/g, 'g').replace(/[а-я]/g, ch => ({ а: 'a', б: 'b', в: 'v', г: 'h', д: 'd', е: 'e', ж: 'zh', з: 'z', и: 'y', й: 'i', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'kh', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'shch', ь: '', ю: 'iu', я: 'ia' }[ch] ?? '')).replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
function pgCategories() {
  const cnt = id => D.products.filter(p => p.cat === id).length;
  return `<div class="panel" style="padding:0;overflow:hidden">
      ${D.categories.map((c, i) => `<div class="cat-row"><span class="grip-ic">${ic('grip')}${i === 0 ? nw(2) : ''}</span><span class="em">${c.emoji}</span><div><b>${esc(c.name)}</b><br><span class="slug">${c.id}</span></div><span style="color:var(--ink-2)">${cnt(c.id)} товарів ${i === 0 ? nw(3) : ''}</span>
        <select class="a-input" aria-label="Слот на головній">${['—', 'Слот 1', 'Слот 2', 'Слот 3', 'Слот 4'].map((s, k) => `<option ${c.quickSlot === k ? 'selected' : ''}>${s}</option>`).join('')}</select>
        <span class="sw" title="Показувати в магазині">${i === 0 ? nw(5, 'top:-8px;left:-10px') : ''}<input type="checkbox" ${c.active !== false ? 'checked' : ''} aria-label="Показувати"><span></span></span></div>`).join('')}
    </div>
    <p style="margin:0;font-size:12.5px;color:var(--ink-3)">${nw(4)} Слот — позиція категорії в рядку чипсів на головній магазину.</p>`;
}
function pgFilaments() {
  const used = f => D.products.filter(p => p.filament).length - (f.id.length % 4);
  const firstGlow = FIL.findIndex(f => /світ/i.test(f.name));
  return `<div class="fil-grid">
    ${FIL.map((f, i) => { const on = A.filAvail[f.id]; return `<div class="fil-card">
      <header><span class="spool" style="background:${f.hex}"></span><div><b>${esc(f.name)}</b><small>${f.hex}${/світ/i.test(f.name) ? ` · світиться ${i === firstGlow ? nw(4) : ''}` : ''}</small></div><button class="icon-btn" style="margin-left:auto;width:30px;height:30px" aria-label="Редагувати">${ic('edit')}</button></header>
      <label class="toggle-row" style="padding:4px 0 0"><span>${on ? 'В наявності' : 'Закінчився'} ${i === 0 ? nw(2) : ''}<small>У ${Math.max(3, used(f))} товарах ${i === 0 ? nw(3) : ''}</small></span><span class="sw"><input type="checkbox" data-a="fil" data-v="${f.id}" ${on ? 'checked' : ''}><span></span></span></label>
    </div>`; }).join('')}
    <button class="fil-add" data-a="new" data-v="filament">${ic('plus')}Додати колір<small style="font-weight:500;color:var(--ink-3)">Назва, HEX, світний</small></button>
  </div>`;
}

// ── Купони, акції ──
const couponValue = c => c.type === 'percent' ? `−${c.value}%` : `−${c.value} ₴`;
const dateUa = d => d ? new Date(d + 'T12:00').toLocaleDateString('uk-UA', { day: 'numeric', month: 'long' }) : '';
function pgCoupons() {
  if (!A.coupons.length) return `<p class="page-empty-hint">Купонів ще немає — натисни «Купон» угорі.</p>`;
  return A.coupons.map((c, i) => `<div class="coupon-card" data-a="edit-coupon" data-v="${i}">
      <div class="tk">${couponValue(c)}</div>
      <div><div class="code">${c.code}</div><div class="meta"><span>${c.min ? `від ${money(c.min)}` : 'будь-яка сума'}</span><span>${c.exp ? `до ${dateUa(c.exp)}` : 'безстроково'}</span>${c.onePer ? '<span>один раз на клієнта</span>' : ''}${c.stack ? '<span>сумується з акцією</span>' : ''}${c.sub ? `<span>лише підписникам ${esc(c.channel || 'каналу')}</span>` : ''}${c.ids.length ? `<span>лише для ${c.ids.length} ${plural(c.ids.length, 'клієнта', 'клієнтів', 'клієнтів')}</span>` : ''} ${i === 0 ? nw(4) : ''}</div>
        <div class="usage"><div style="display:flex;justify-content:space-between"><span>Використано ${c.used}${c.max ? ' з ' + c.max : ''} · принесло <b style="color:var(--ink)">${money(c.rev)}</b> ${i === 0 ? nw(3) : ''}</span></div><div class="bar"><i style="width:${c.max ? Math.min(100, c.used / c.max * 100) : 35}%"></i></div></div>
        <div class="link-box" data-stop>${ic('link')}<span>t.me/poselyanov3dprint_bot?startapp=coupon_${c.code}</span><button class="btn sm soft" data-a="toast" data-v="Посилання скопійовано">Копіювати</button>${i === 0 ? nw(2) : ''}</div></div>
      <label class="sw" title="Активний" data-stop><input type="checkbox" data-a="coupon-on" data-v="${i}" ${c.active ? 'checked' : ''} aria-label="Активний"><span></span></label>
    </div>`).join('');
}
function pgPromos() {
  const st = A.promos.map(promoStatus);
  const cnt = k => k === 'all' ? A.promos.length : st.filter(s => s === k).length;
  const tabs = [['all', 'Усі'], ['active', 'Діють'], ['scheduled', 'Заплановані'], ['ended', 'Завершені'], ['off', 'Вимкнені']];
  const list = A.promos.map((p, i) => [p, i]).filter(([p]) => A.promoTab === 'all' || promoStatus(p) === A.promoTab);
  const sale = D.products.filter(p => !p.contract && p.oldPrice && A.prices[p.id] < p.oldPrice);
  return `<div class="toolbar"><div class="tabs-line">${nw(4)}${tabs.map(([id, l]) => `<button class="tab-pill" data-a="promotab" data-v="${id}" aria-pressed="${A.promoTab === id}">${l}<span class="c">${cnt(id)}</span></button>`).join('')}</div></div>
  ${list.map(([p, i], k) => { const s = PROMO_ST[promoStatus(p)]; return `<div class="promo-admin promo-list-card" data-a="edit-promo" data-v="${i}">
    <div style="position:relative">${k === 0 ? nw(2, 'top:-6px;right:-6px') : ''}${promoHero(p)}</div>
    <div style="display:grid;gap:10px">
      <div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><div><b style="font-size:15px">${esc(p.title)}</b><div style="font-size:12.5px;color:var(--ink-2);margin-top:2px">${promoCond(p)}</div></div>
        <span style="display:flex;gap:10px;align-items:center"><span class="st ${s[0]}"><i></i>${s[1]}</span><label class="sw" data-stop><input type="checkbox" data-a="promo-on" data-v="${i}" ${p.active ? 'checked' : ''} aria-label="Активна"><span></span></label></span></div>
      <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--ink-2)"><span>${ic('calendar').replace('class="i ', 'style="width:14px;height:14px;vertical-align:-2px" class="i ')} ${p.starts ? dateUa(p.starts) : 'одразу'} — ${p.ends ? dateUa(p.ends) : 'без кінця'}</span>${p.timer && p.ends ? `<span>${ic('clock').replace('class="i ', 'style="width:14px;height:14px;vertical-align:-2px" class="i ')} таймер у магазині ${k === 0 ? nw(3) : ''}</span>` : ''}</div>
      ${p.stats ? `<div class="profit">${k === 0 ? nw(5) : ''}<div><small>Замовлень з акцією</small><b>${p.stats[0]}</b></div><div><small>Виторг</small><b>${money(p.stats[1])}</b></div><div><small>${p.type === 'gift_threshold' ? 'Подарунків' : 'Знижок'}</small><b>${p.stats[2]}</b></div></div>` : `<div style="font-size:12.5px;color:var(--ink-3)">Статистика зʼявиться після першого замовлення з акцією</div>`}
    </div></div>`; }).join('') || `<p class="page-empty-hint">Тут поки порожньо.</p>`}
  <div class="panel" style="display:flex;gap:14px;align-items:center;flex-wrap:wrap">
    ${nw(6)}<div style="flex:1;min-width:220px"><b>Знижки на окремі товари</b><div style="font-size:12.5px;color:var(--ink-2)">${sale.length} ${plural(sale.length, 'товар має', 'товари мають', 'товарів мають')} стару ціну — у магазині показуються з −%</div></div>
    <div class="thumbs">${sale.slice(0, 5).map(p => `<img src="${img(p)}" alt="">`).join('')}</div>
    <button class="btn sm soft" data-a="go-sale">Відкрити «Акційні»</button>
  </div>`;
}

// ── Клієнти, розсилки ──
const SEGS = [['all', 'Усі', 412], ['buyers', 'Купували', 58], ['nobuy', 'Не купували', 354], ['sleep', 'Неактивні 30+ днів', 120], ['vip', 'Від 1000 ₴', 9]];
function pgClients() {
  const rows = [['Олена К.', '@olena_k', 5, 2140, 'сьогодні'], ['Тарас Б.', '@taras_b', 3, 1650, '3 дні тому'], ['Наталія В.', '@natali_v', 2, 500, '5 днів тому'], ['Ігор П.', '@igor_p', 1, 350, 'вчора'], ['Софія Л.', '@sofiia', 1, 300, 'вчора'], ['Андрій М.', '@andrii_m', 1, 495, '12 хв тому']];
  return `<div class="seg-chips">${nw(1)}${SEGS.map(([id, l, n]) => `<button class="seg-card" data-a="seg" data-v="${id}" aria-pressed="${A.seg === id}"><b>${n}</b><small>${l}</small></button>`).join('')}</div>
  <div class="table-wrap"><table class="t"><thead><tr><th>Клієнт ${nw(2)}</th><th>Замовлень</th><th>Сума</th><th>Остання активність</th><th style="text-align:right">Дії</th></tr></thead><tbody>
    ${rows.map(([n, u, o, s, l], i) => `<tr><td><div class="p-cell" style="min-width:160px"><span class="review"><span class="av" style="background:hsl(${i * 57 + 200} 65% 55%)">${n[0]}</span></span><div><b>${n}</b><small>${u}</small></div></div></td><td class="tnum">${o}</td><td class="tnum"><b>${money(s)}</b></td><td style="color:var(--ink-2)">${l}</td>
    <td style="text-align:right;white-space:nowrap"><button class="btn sm soft" data-a="toast" data-v="Купон PERS-${u.slice(1).toUpperCase()} на −10% надіслано">${ic('ticket')}Купон</button>${i === 0 ? nw(3) : ''} <button class="btn sm ghost">${ic('send')}Написати</button>${i === 0 ? nw(4) : ''}</td></tr>`).join('')}
  </tbody></table></div>`;
}
function pgBroadcast() {
  const seg = SEGS.find(s => s[0] === A.seg);
  return `<div class="cols-2">
    <div class="panel" style="display:grid;gap:12px">
      <div class="f"><label>Кому ${nw(1)}</label><div class="seg-chips">${SEGS.map(([id, l, n]) => `<button class="tab-pill" data-a="seg" data-v="${id}" aria-pressed="${A.seg === id}">${l}<span class="c">${n}</span></button>`).join('')}</div></div>
      <div class="f"><label for="btext">Текст ${nw(2)}</label><textarea class="a-input" id="btext" rows="4">${esc(A.bText)}</textarea></div>
      <div class="f-grid"><div class="f"><label>Фото</label><div class="p-cell" style="min-width:0"><img src="${img(PROD[134])}" alt=""><button class="btn sm ghost">${ic('image')}Замінити</button></div></div>
        <div class="f"><label for="bbtn">Кнопка</label><input class="a-input" id="bbtn" value="${esc(A.bBtn)}"><span class="hint">Веде на добірку «Світяться вночі»</span></div></div>
      <div class="f-grid"><div class="f"><label for="bwhen">Коли ${nw(4)}</label><select class="a-input" id="bwhen"><option>Одразу</option><option>Завтра о 10:00</option><option>Інший час…</option></select></div><div class="f"><label>&nbsp;</label><button class="btn primary" data-a="toast" data-v="Розсилку поставлено в чергу: ${seg[2]} отримувачів">${ic('send')}Надіслати ${seg[2]} клієнтам</button></div></div>
    </div>
    <div class="panel"><h3>Превʼю ${nw(3)}<small>так побачить клієнт</small></h3>
      <div class="chat-bg"><div class="tg-bubble"><img src="${img(PROD[134])}" alt=""><div class="tx">${esc(A.bText)}</div><div class="kb">${esc(A.bBtn)}</div></div></div></div>
  </div>
  <div class="table-wrap"><table class="t"><thead><tr><th>Розсилка ${nw(5)}</th><th>Кому</th><th>Доставлено</th><th>Перейшли</th><th>Замовлень</th></tr></thead><tbody>
    <tr><td><b>Осінні знижки −10%</b><br><small style="color:var(--ink-3)">21 вересня</small></td><td>Усі</td><td class="tnum">398</td><td class="tnum">71 · 18%</td><td class="tnum"><b>9</b> · ${money(2480)}</td></tr>
    <tr><td><b>Нові флексі-тваринки</b><br><small style="color:var(--ink-3)">8 вересня</small></td><td>Купували</td><td class="tnum">55</td><td class="tnum">24 · 44%</td><td class="tnum"><b>6</b> · ${money(1150)}</td></tr>
  </tbody></table></div>`;
}

// ── Складання ──
const PAGES = { overview: pgOverview, orders: pgOrders, queue: pgQueue, products: pgProducts, categories: pgCategories, filaments: pgFilaments, coupons: pgCoupons, promos: pgPromos, clients: pgClients, broadcast: pgBroadcast };
function renderAdmin() {
  const root = document.getElementById('admin-root');
  const prevScroll = root.querySelector('.a-content')?.scrollTop;
  const notes = NOTES_A[A.page] || [];
  const qFocus = document.activeElement?.id === 'prod-q';
  const act = PAGE_ACTION[A.page];
  root.innerHTML = `
  <div class="adm-notes"><span class="lbl">Що нового тут</span><ol>${notes.map((t, i) => `<li><span class="nw">${i + 1}</span>${t}</li>`).join('')}</ol></div>
  <div class="adm">
    <aside class="a-side">
      <div class="a-brand"><div class="brand-mark">3D</div><div><b>Poselyanov 3D Print</b><small>Адмінка</small></div></div>
      ${NAV.map(n => n[0] === 'g' ? `<div class="a-nav-group">${n[1]}</div>` : `<button class="a-nav" data-a="go" data-v="${n[0]}" ${A.page === n[0] ? 'aria-current="page"' : ''}>${ic(n[2])}${n[1]}${n[3] ? `<span class="cnt${n[4] ? ' hot' : ''}">${n[3]()}</span>` : ''}${A.page === 'overview' && n[0] === 'orders' ? nw(6) : ''}</button>`).join('')}
      <div class="a-side-foot"><span>Бот працює · 82 товари в магазині</span><button class="btn sm ghost" data-a="toast" data-v="Відкрито магазин">${ic('store')}Відкрити магазин</button></div>
    </aside>
    <div class="a-main">
      <div class="a-top"><h1>${TITLES[A.page]} ${A.page === 'overview' ? nw(8) : ''}</h1><span class="sp"></span>
        <div class="kbd-search">${ic('search')}Шукати товар, замовлення, клієнта<kbd>Ctrl K</kbd></div>${A.page === 'overview' ? nw(7) : ''}
        <button class="icon-btn" aria-label="Сповіщення" style="position:relative">${ic('bell')}<span style="position:absolute;top:6px;right:7px;width:8px;height:8px;border-radius:50%;background:var(--orange)"></span></button>
        ${act ? `${nw(act.m)}<button class="btn sm ${act.ghost ? 'ghost' : 'primary'}" data-a="${act.a}" data-v="${act.v}">${ic(act.icon)}${act.label}</button>` : ''}</div>
      <div class="a-content">${PAGES[A.page]()}</div>
    </div>
  </div>`;
  if (prevScroll) root.querySelector('.a-content').scrollTop = prevScroll;
  const q = document.getElementById('prod-q');
  if (q) { q.oninput = () => { A.prodQ = q.value; renderAdmin(); }; if (qFocus) { q.focus(); q.setSelectionRange(q.value.length, q.value.length); } }
  const bt = document.getElementById('btext');
  if (bt) bt.oninput = () => { A.bText = bt.value; const tx = document.querySelector('.tg-bubble .tx'); if (tx) tx.textContent = bt.value; };
  const bb = document.getElementById('bbtn');
  if (bb) bb.oninput = () => { A.bBtn = bb.value; const kb = document.querySelector('.tg-bubble .kb'); if (kb) kb.textContent = bb.value; };
}
function renderDrawer() {
  const body = document.querySelector('#drawer-root .drawer-body');
  const st = body && A.drawer ? body.scrollTop : 0;
  document.getElementById('drawer-root').innerHTML = renderDrawerBody();
  const nb = document.querySelector('#drawer-root .drawer-body'); if (nb) nb.scrollTop = st;
  if (!A.drawer) renderAdmin();
  document.body.style.overflow = A.drawer ? 'hidden' : '';
}
let aToastT;
function aToast(msg) {
  let t = document.getElementById('a-toast');
  if (!t) { t = document.createElement('div'); t.id = 'a-toast'; t.className = 'toast'; t.style.cssText = 'position:fixed;left:50%;right:auto;bottom:28px;transform:translateX(-50%);z-index:120;max-width:calc(100% - 32px)'; document.body.appendChild(t); }
  t.innerHTML = `${ic('check')}<span>${esc(msg)}</span>`;
  t.hidden = false;
  clearTimeout(aToastT); aToastT = setTimeout(() => { t.hidden = true; }, 2200);
}
function onAdminClick(e) {
  const el = e.target.closest('[data-a]');
  if (!el) { if (A.prodMenu || A.oMenu) { A.prodMenu = null; A.oMenu = false; renderAdmin(); } return; }
  const a = el.dataset.a, v = el.dataset.v;
  // клік у зоні data-stop (перемикач, чекбокс) не відкриває картку/рядок навколо
  const stop = e.target.closest('[data-stop]');
  if (stop && !stop.contains(el)) return;
  if (['psel', 'selall', 'qdone', 'fil', 'price', 'dirty', 'eprice', 'slug', 'promo-on', 'coupon-on'].includes(a)) return; // обробляються в change/input
  const d = A.drawer?.k;
  // перед перерендером шухляди зберегти введене
  if (d === 'promo' && ['preset', 'ptype'].includes(a)) readForm(A.pf);
  if (d === 'coupon' && ['ctype', 'gen', 'cid-add', 'cid-rm'].includes(a)) readForm(A.cf);
  if (d === 'category' && a === 'cat-emoji') readForm(A.catf);
  if (d === 'order' && ['osrc', 'oi-add', 'oi-rm'].includes(a)) readForm(A.of);
  switch (a) {
    case 'new': openDrawer(v); return;
    case 'edit-promo': openDrawer('promo', Number(v)); return;
    case 'edit-coupon': openDrawer('coupon', Number(v)); return;
    case 'preset': { const pr = PRESETS.flatMap(g => g[1]).find(x => x[0] === v); A.pf = { ...PROMO_BLANK, ...pr[2], preset: v, starts: A.pf.starts, ends: A.pf.ends, accent: A.pf.accent }; A.dirty = true; renderDrawer(); return; }
    case 'ptype': A.pf.type = v; A.dirty = true; renderDrawer(); return;
    case 'pacc': {
      readForm(A.pf); A.pf.accent = v;
      el.parentNode.querySelectorAll('[data-a="pacc"]').forEach(b => b.setAttribute('aria-pressed', String(b === el)));
      const pv = document.getElementById('pf-preview'); if (pv) pv.innerHTML = promoHero(A.pf);
      markDirty(); return;
    }
    case 'ctype': A.cf.type = v; A.dirty = true; renderDrawer(); return;
    case 'cid-add': if (!A.cf.ids.includes(v)) A.cf.ids.push(v); A.dirty = true; renderDrawer(); return;
    case 'cid-rm': A.cf.ids.splice(Number(v), 1); A.dirty = true; renderDrawer(); return;
    case 'cat-emoji': A.catf.emoji = v; A.dirty = true; renderDrawer(); return;
    case 'osrc': A.of.src = v; renderDrawer(); return;
    case 'oi-add': A.of.items.push([11, 1, 'black']); A.dirty = true; renderDrawer(); return;
    case 'oi-rm': if (A.of.items.length > 1) A.of.items.splice(Number(v), 1); renderDrawer(); return;
    case 'promotab': A.promoTab = v; break;
    case 'psale': A.prodSale = !A.prodSale; break;
    case 'go-sale': A.page = 'products'; A.prodTab = 'active'; A.prodSale = true; break;
    case 'bnew': A.bText = ''; A.bBtn = 'Переглянути'; renderAdmin(); document.getElementById('btext')?.focus(); return;
    case 'go': A.page = v; A.prodMenu = null; A.drawer = null; renderDrawer(); renderAdmin(); document.getElementById('view-admin').scrollIntoView({ block: 'start' }); break;
    case 'period': A.period = Number(v); break;
    case 'ofilter': A.oFilter = v; break;
    case 'oview': A.oView = v; break;
    case 'order': A.page = 'orders'; A.oSel = Number(v); A.oView = 'list'; A.tplSel = null; A.oMenu = false; break;
    case 'omenu': A.oMenu = !A.oMenu; break;
    case 'ostep': { const o = ORDERS.find(x => x.n === A.oSel); o.st = v; aToast(`№${o.n}: ${ST[v][1]} — клієнту надіслано повідомлення`); break; }
    case 'tpl': A.tplSel = A.tplSel === v ? null : v; break;
    case 'ptab': A.prodTab = v; A.prodSel.clear(); break;
    case 'flag': { const f = A.flags[Number(v)]; f[el.dataset.f] = !f[el.dataset.f]; break; }
    case 'pmenu': A.prodMenu = A.prodMenu === Number(v) ? null : Number(v); break;
    case 'pst': A.pstatus[Number(v)] = el.dataset.s; A.prodMenu = null; aToast(el.dataset.s === 'active' ? 'Товар повернуто в магазин' : el.dataset.s === 'hidden' ? 'Товар приховано з магазину' : 'Товар переміщено в архів'); break;
    case 'dup': A.prodMenu = null; aToast(`Створено копію «${PROD[Number(v)].name}»`); break;
    case 'edit': openDrawer('product', v === 'new' ? null : Number(v)); return;
    case 'close-drawer-back': if (e.target !== el) return; // клік по фону
    // falls through
    case 'close-drawer': A.drawer = null; renderDrawer(); return;
    case 'save': saveDrawer(); break;
    case 'bulkdisc': { const pct = Number(document.getElementById('bulk-pct')?.value) || A.bulkPct; A.bulkPct = pct; A.prodSel.forEach(id => { const p = PROD[id]; if (!p.contract) { if (!p.oldPrice) p.oldPrice = A.prices[id]; A.prices[id] = Math.round(p.oldPrice * (1 - pct / 100) / 5) * 5; } }); aToast(`Знижку −${pct}% застосовано до ${A.prodSel.size} товарів`); A.prodSel.clear(); break; }
    case 'bulkhide': A.prodSel.forEach(id => { A.pstatus[id] = 'hidden'; }); aToast(`Приховано ${A.prodSel.size} товарів`); A.prodSel.clear(); break;
    case 'bulkclear': A.prodSel.clear(); break;
    case 'gen': A.cf.code = ['OSIN', 'PRINT', 'CUBE', 'FLEXI', 'GLOW'][Math.floor(Math.random() * 5)] + (10 + Math.floor(Math.random() * 40)); A.dirty = true; renderDrawer(); return;
    case 'seg': A.seg = v; break;
    case 'toast': aToast(v); if (A.prodMenu || A.oMenu) { A.prodMenu = null; A.oMenu = false; break; } return;
    default: return;
  }
  renderAdmin();
}
document.getElementById('admin-root').addEventListener('click', onAdminClick);
document.getElementById('drawer-root').addEventListener('click', onAdminClick);
document.addEventListener('change', e => {
  const el = e.target.closest('[data-a]');
  if (!el) return;
  const a = el.dataset.a, v = el.dataset.v;
  if (a === 'psel') { const id = Number(v); A.prodSel.has(id) ? A.prodSel.delete(id) : A.prodSel.add(id); renderAdmin(); }
  if (a === 'selall') { const list = D.products.filter(p => A.pstatus[p.id] === A.prodTab); if (el.checked) list.forEach(p => A.prodSel.add(p.id)); else A.prodSel.clear(); renderAdmin(); }
  if (a === 'qdone') { A.queueDone.has(v) ? A.queueDone.delete(v) : A.queueDone.add(v); const n = Number(v.split('-')[0]); const all = queueRows().filter(r => r.n === n).every(r => A.queueDone.has(r.key)); if (all) { const o = ORDERS.find(x => x.n === n); o.st = 'ready'; aToast(`№${n} повністю надруковано — статус «Готове»`); } renderAdmin(); }
  if (a === 'fil') { A.filAvail[v] = el.checked; aToast(el.checked ? 'Колір знову доступний у магазині' : 'Колір приховано з вибору в магазині'); renderAdmin(); }
  if (a === 'price') { A.prices[Number(v)] = Number(el.value) || 0; aToast('Ціну оновлено в магазині'); }
  if (a === 'promo-on') { A.promos[Number(v)].active = el.checked; aToast(el.checked ? 'Акцію увімкнено — вона вже в магазині' : 'Акцію вимкнено'); renderAdmin(); }
  if (a === 'coupon-on') { A.coupons[Number(v)].active = el.checked; aToast(el.checked ? 'Купон активний' : 'Купон вимкнено'); }
});
function saveDrawer() {
  const { k, id } = A.drawer;
  let msg = 'Збережено';
  if (k === 'product') msg = 'Товар збережено';
  if (k === 'promo') {
    readForm(A.pf);
    const p = { ...A.pf, id: A.pf.id || slugify(A.pf.title) || 'promo_' + Date.now() };
    if (id == null) { A.promos.unshift(p); msg = `Акцію «${p.title || 'без назви'}» створено`; } else { A.promos[id] = p; msg = 'Акцію збережено'; }
    A.page = 'promos'; A.promoTab = 'all';
  }
  if (k === 'coupon') {
    readForm(A.cf);
    if (id == null) { A.coupons.unshift({ ...A.cf }); msg = `Купон ${A.cf.code} створено`; } else { A.coupons[id] = { ...A.cf }; msg = 'Купон збережено'; }
  }
  if (k === 'category') { readForm(A.catf); msg = `Категорію «${A.catf.name}» додано`; }
  if (k === 'filament') { readForm(A.filf); msg = `Колір «${A.filf.name}» додано`; }
  if (k === 'order') { readForm(A.of); msg = 'Замовлення №1144 створено'; }
  A.drawer = null; A.dirty = false; renderDrawer(); aToast(msg);
}
function markDirty() {
  if (A.dirty) return;
  A.dirty = true;
  const foot = document.querySelector('.drawer-foot > span:first-child');
  if (foot) foot.outerHTML = `<span class="unsaved"><i></i>Є незбережені зміни ${A.drawer?.k === 'product' ? nw(9) : ''}</span>`;
}
document.addEventListener('input', e => {
  const el = e.target.closest('#drawer-root [data-a], #drawer-root input, #drawer-root textarea, #drawer-root select');
  if (!el) return;
  const k = A.drawer?.k;
  markDirty();
  // живе превʼю без перерендеру форми
  if (k === 'promo') { readForm(A.pf); const pv = document.getElementById('pf-preview'); if (pv) pv.innerHTML = promoHero(A.pf); const c = document.getElementById('pf-cond'); if (c) c.textContent = promoCond(A.pf); }
  if (k === 'coupon' && el.id !== 'cid-in') { readForm(A.cf); const pv = document.getElementById('cf-preview'); if (pv) pv.innerHTML = couponPreview(A.cf); }
  if (k === 'category') { readForm(A.catf); document.getElementById('catf-slug').textContent = slugify(A.catf.name) || '—'; document.getElementById('catf-preview').innerHTML = catPreview(A.catf); }
  if (k === 'filament') {
    if (el.dataset.f === 'hex') { const t = document.querySelector('#drawer-root [data-f="hexText"]'); if (t) t.value = el.value; }
    readForm(A.filf);
    if (el.dataset.f === 'hexText') { const c = document.querySelector('#drawer-root [data-f="hex"]'); if (c) c.value = A.filf.hex; }
    document.getElementById('filf-preview').innerHTML = filPreview(A.filf);
  }
  if (k === 'order' && el.dataset.oi != null) {
    const it = A.of.items[Number(el.dataset.oi)];
    if (el.dataset.k === 'id') it[0] = Number(el.value); else if (el.dataset.k === 'q') it[1] = Math.max(1, Number(el.value) || 1); else it[2] = el.value;
    document.getElementById('of-total').textContent = money(A.of.items.reduce((s, [id, q]) => s + (PROD[id].contract ? 0 : PROD[id].price * q), 0));
  }
  if (el.dataset.a === 'eprice' && k === 'product' && A.drawer.id != null) {
    const p = PROD[A.drawer.id]; const price = Number(el.value) || 0; const box = document.getElementById('auto-disc');
    const old = Number(document.getElementById('e-old').value) || 0;
    if (box) box.innerHTML = old > price ? `<span class="auto-disc">Знижка −${Math.round((1 - price / old) * 100)}% · покупець економить ${money(old - price)}</span>` : '<span class="hint" style="font-size:12px;color:var(--ink-3)">Вкажи стару ціну — знижка у відсотках порахується сама</span>';
    const pv = document.querySelector('.preview-phone'); if (pv) pv.innerHTML = card({ ...p, price, oldPrice: old || null });
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && A.drawer) { A.drawer = null; renderDrawer(); }
  if (e.key === 'Enter' && e.target.id === 'cid-in') {
    e.preventDefault();
    const ids = e.target.value.split(/[\s,]+/).filter(x => /^\d{5,12}$/.test(x));
    if (!ids.length) { aToast('Telegram ID — це лише цифри'); return; }
    readForm(A.cf); ids.forEach(x => { if (!A.cf.ids.includes(x)) A.cf.ids.push(x); }); A.dirty = true; renderDrawer();
    document.getElementById('cid-in')?.focus();
  }
});

try { if (localStorage.getItem('p3d-mode') === 'admin') setMode('admin'); } catch (_) {}
