// StudyBot Mini App: неделя, ближайший дедлайн, задания по дням и карточка. Данные — через API бота (/api/tasks).
"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const inTg = !!(tg && tg.initData);
const $ = (id) => document.getElementById(id);
const root = document.documentElement;

const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const SRC = { portal: "СДО", tg: "из чата", manual: "вручную" };
const LINK = {
  portal: ["ti-school", "Открыть в СДО"],
  tg: ["ti-message", "Открыть сообщение в группе"],
  manual: ["ti-link", "Открыть ссылку"],
};
// Цвета точек предметов — различимы и на светлом, и на тёмном фоне
const DOTS = ["#6366F1", "#10B981", "#F59E0B", "#EC4899", "#0EA5E9", "#8B5CF6"];
// [фон карточки «Ближайший дедлайн», акцент] — для кружков в выборе оформления
const PALETTES = [
  { id: "indigo", name: "Индиго", light: ["#1E1B4B", "#4F46E5"], dark: ["#1C2230", "#C6F36B"] },
  { id: "mint", name: "Мята", light: ["#0B3B36", "#0F766E"], dark: ["#10231F", "#5EEAD4"] },
  { id: "sunset", name: "Закат", light: ["#3B1426", "#C2410C"], dark: ["#2A1420", "#FB923C"] },
  { id: "ocean", name: "Океан", light: ["#082F49", "#0369A1"], dark: ["#0C2233", "#38BDF8"] },
  { id: "graphite", name: "Графит", light: ["#18181B", "#FACC15"], dark: ["#1F1F23", "#FACC15"] },
];
const FILTER_KEY = "subject_filter";
const THEME_KEY = "theme";

let tasks = [];
let subjects = [];
let filter = null;    // предмет
let dayFilter = null; // 0…6 — день недели в полоске, null — все
let current = null;   // задание в карточке; {} — новое
let theme = { palette: "indigo", mode: "auto" };

// ---------- хранение настроек ----------

// CloudStorage есть с версии 6.9; в старом Telegram Desktop метод бросает исключение или не отвечает вовсе
const hasBack = () => inTg && !!tg.BackButton && (!tg.isVersionAtLeast || tg.isVersionAtLeast("6.1"));
const hasCloud = () => inTg && !!tg.CloudStorage && (!tg.isVersionAtLeast || tg.isVersionAtLeast("6.9"));

function store(key, value) {
  try { if (hasCloud()) tg.CloudStorage.setItem(key, value, () => {}); } catch (e) { /* нет облака — хватит localStorage */ }
  try { localStorage.setItem(key, value); } catch (e) { /* приватный режим — не страшно */ }
}
function restore(key) {
  const local = () => { try { return localStorage.getItem(key); } catch (e) { return null; } };
  return new Promise((resolve) => {
    if (!hasCloud()) return resolve(local());
    const timer = setTimeout(() => resolve(local()), 1500); // облако молчит — не держим приложение
    try {
      tg.CloudStorage.getItem(key, (err, v) => { clearTimeout(timer); resolve(err ? local() : v || local()); });
    } catch (e) {
      clearTimeout(timer);
      resolve(local());
    }
  });
}
// Вызов метода Telegram, которого может не быть в старом клиенте
function tgSafe(fn) {
  try { fn(); } catch (e) { /* старый клиент — без этой мелочи */ }
}

// ---------- оформление ----------

function isDark() {
  if (theme.mode !== "auto") return theme.mode === "dark";
  return inTg ? tg.colorScheme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
const cssVar = (name) => getComputedStyle(root).getPropertyValue(name).trim();

function applyTheme() {
  root.classList.toggle("dark", isDark());
  root.dataset.palette = PALETTES.some((p) => p.id === theme.palette) ? theme.palette : "indigo";
  if (!inTg) return;
  const bg = cssVar("--bg");
  for (const fn of ["setHeaderColor", "setBackgroundColor", "setBottomBarColor"]) {
    try { if (tg[fn]) tg[fn](bg); } catch (e) { /* старый клиент Telegram */ }
  }
  try { tg.MainButton.setParams({ color: cssVar("--accent"), text_color: cssVar("--on-accent") }); } catch (e) { /* то же */ }
}

function renderSheet() {
  const dark = isDark();
  $("palettes").innerHTML = PALETTES.map((p) => {
    const [a, b] = p[dark ? "dark" : "light"];
    const on = p.id === root.dataset.palette;
    return `<button class="pal ${on ? "on" : ""}" data-pal="${p.id}" aria-pressed="${on}">
      <span class="sw" style="background:${a}"><i style="background:${b}"></i></span>${p.name}</button>`;
  }).join("");
  for (const b of $("modes").children) {
    b.classList.toggle("on", b.dataset.mode === theme.mode);
    b.setAttribute("aria-pressed", b.dataset.mode === theme.mode);
  }
}
function setTheme(patch) {
  theme = { ...theme, ...patch };
  store(THEME_KEY, JSON.stringify(theme));
  applyTheme();
  renderSheet();
  haptic("select");
}
function openSheet() {
  renderSheet();
  $("theme-sheet").hidden = false;
  if (inTg) tgSafe(() => tg.BackButton.show());
}
function closeSheet() {
  $("theme-sheet").hidden = true;
  if (inTg && !current) tgSafe(() => tg.BackButton.hide());
}

// ---------- мелочи ----------

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => (Number.isInteger(n) ? String(n) : String(n).replace(".", ","));
function pl(n, one, few, many) {
  n = Math.floor(Math.abs(n));
  const a = n % 10, b = n % 100;
  return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many;
}
const plural = (n) => pl(n, "балл", "балла", "баллов");
function dotOf(s) {
  if (!s) return "var(--check)";
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return DOTS[h % DOTS.length];
}
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

// ---------- сроки ----------

const deadlineOf = (t) => (t.deadline ? new Date(t.deadline) : null);
const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
const dayDiff = (d, now) => Math.round((startOfDay(d) - startOfDay(now)) / 864e5);
const hm = (d) => d.toTimeString().slice(0, 5);
const dayMon = (d) => `${d.getDate()} ${MON[d.getMonth()]}`;
const byDeadline = (a, b) => (deadlineOf(a) || 0) - (deadlineOf(b) || 0);

function timeLeft(d, now) {
  const min = Math.max(1, Math.round((d - now) / 60000));
  if (min < 60) return `${min} мин`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} ч ${min % 60} мин`;
  return `${Math.floor(h / 24)} дн ${h % 24} ч`;
}
function dueLabel(d, now) {
  const k = dayDiff(d, now);
  if (k === 0) return `до ${hm(d)}`;
  if (k === 1) return `завтра, до ${hm(d)}`;
  return `до ${WD[d.getDay()]}, ${dayMon(d)}, ${hm(d)}`;
}
function lateLabel(d, now) {
  const k = -dayDiff(d, now);
  if (k <= 0) {
    const h = Math.max(1, Math.round((now - d) / 36e5));
    return `Просрочено на ${h} ч`;
  }
  return `Просрочено на ${k} ${pl(k, "день", "дня", "дней")}`;
}

// ---------- статус из СДО ----------

function statusOf(t) {
  if (t.source !== "portal" || !t.status) return null;
  const pts = t.points != null ? fmtNum(t.points) : "?";
  switch (t.status) {
    case "draft": return { cls: "st-warn", short: "Черновик не отправлен", ans: "Черновик (не отправлен)", grade: "Не оценено" };
    case "submitted": return { cls: "st-info", short: "Ждёт оценки", ans: "Отправлено для оценивания", grade: "Не оценено" };
    case "graded": {
      const g = t.grade != null ? `Оценено: ${fmtNum(t.grade)} из ${pts}` : "Оценено";
      return { cls: "st-ok", short: g, ans: "Отправлено для оценивания", grade: g };
    }
    case "quiz": {
      if (t.attempts_total == null) return null;
      const used = t.attempts_used ?? 0;
      const best = t.grade != null ? ` · ${fmtNum(t.grade)} из ${pts}` : "";
      return {
        cls: "st-info", short: used ? `Попытка ${used} из ${t.attempts_total}${best}` : "",
        ans: `Использовано попыток: ${used} из ${t.attempts_total}`,
        grade: t.grade != null ? `Лучший результат: ${fmtNum(t.grade)} из ${pts}` : "Попыток пока нет",
      };
    }
    default: return { cls: "", short: "", ans: "Ответы на задание еще не представлены", grade: "Не оценено" };
  }
}

function ago(iso) {
  if (!iso) return "ещё не проверялось";
  const min = Math.max(0, Math.round((Date.now() - new Date(iso)) / 60000));
  if (min < 1) return "проверено только что";
  if (min < 60) return `проверено ${min} мин назад`;
  const h = Math.round(min / 60);
  return h < 24 ? `проверено ${h} ч назад` : `проверено ${Math.round(h / 24)} дн назад`;
}

// ---------- API ----------

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (inTg) headers.Authorization = "tma " + tg.initData;
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error((await r.text()) || `Ошибка ${r.status}`);
  return r.status === 204 ? null : r.json();
}

async function load() {
  try {
    const data = await api("GET", "/api/tasks");
    tasks = data.tasks;
    subjects = data.subjects;
    $("error").hidden = true;
  } catch (e) {
    $("error").textContent = e.message === "Failed to fetch" ? "Нет связи с ботом. Попробуй ещё раз." : e.message;
    $("error").hidden = false;
  }
}

// ---------- список ----------

const ptsTag = (t) => (t.points ? `<span class="pts">+${fmtNum(t.points)} б.</span>` : "");
function stTag(t) {
  const st = statusOf(t);
  return st && st.short ? `<span class="st ${st.cls}">${esc(st.short)}</span>` : "";
}
const chk = (t) => `<button class="chk ${t.done ? "on" : ""}" data-done="${t.id}" aria-label="${t.done ? "Вернуть в работу" : "Отметить сделанным"}"><i class="ti ti-check"></i></button>`;

function card(t, time) {
  return `<div class="card ${t.done ? "done" : ""}" data-id="${t.id}">${chk(t)}
    <div class="cbody">
      <div class="crow"><span class="subj"><span class="dot" style="background:${dotOf(t.subject)}"></span><span>${esc(t.subject || SRC[t.source] || "")}</span></span>
        <span class="time">${esc(time)}</span></div>
      <span class="ctitle">${esc(t.title)}</span>
      <div class="tags">${ptsTag(t)}${stTag(t)}</div>
    </div></div>`;
}
function group(label, sub, items, timeOf, now = false) {
  if (!items.length) return "";
  return `<section class="group"><div class="glabel ${now ? "now" : ""}"><b>${label}</b><span>${sub}</span></div>
    <div class="gitems">${items.map((t) => card(t, timeOf(t))).join("")}</div></section>`;
}

function renderHeader(vis, open, upcoming, late, now) {
  $("today-label").textContent = cap(now.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" }));
  const inWeek = (t) => { const d = deadlineOf(t); if (!d) return false; const k = dayDiff(d, now); return k >= 0 && k < 7; };
  const today = upcoming.filter((t) => dayDiff(deadlineOf(t), now) === 0).length;
  const week = upcoming.filter(inWeek);
  $("headline").textContent =
    today ? `Сегодня ${today} ${pl(today, "дедлайн", "дедлайна", "дедлайнов")}`
    : late.length ? `Просрочено: ${late.length}`
    : week.length ? `На неделе ${week.length} ${pl(week.length, "дело", "дела", "дел")}`
    : open.length ? "Срочного нет" : "Всё сдано";

  const sum = Math.round(week.reduce((a, t) => a + (t.points || 0), 0) * 10) / 10;
  $("sum").hidden = !sum;
  $("sum").textContent = `можно набрать ${fmtNum(sum)} ${plural(sum)} за неделю`;

  const all = vis.filter(inWeek);
  const done = all.filter((t) => t.done).length;
  $("ring").toggleAttribute("hidden", !all.length); // у SVG нет свойства .hidden
  $("ring-fg").setAttribute("stroke-dasharray", `${(132 * done) / (all.length || 1)} 132`);
  $("ring-text").textContent = `${done}/${all.length}`;
  $("ring").setAttribute("aria-label", `Сделано ${done} из ${all.length} за неделю`);
}

function renderWeek(open, now) {
  let html = "";
  for (let i = 0; i < 7; i++) {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
    const n = open.filter((t) => { const x = deadlineOf(t); return x && x >= now && dayDiff(x, now) === i; }).length;
    const on = dayFilter === i;
    html += `<button class="day ${i === 0 ? "today" : ""} ${on ? "on" : ""}" data-day="${i}" aria-pressed="${on}" aria-label="${d.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}: ${n} ${pl(n, "задание", "задания", "заданий")}">
      <span class="wd">${WD[d.getDay()]}</span><span class="n">${d.getDate()}</span><span class="dots">${"•".repeat(Math.min(n, 3))}</span></button>`;
  }
  $("week").innerHTML = html;
}

function renderHero(t, now) {
  if (!t) { $("hero").innerHTML = ""; return; }
  const d = deadlineOf(t);
  const st = t.status === "draft"
    ? `<div class="hero-warn"><i class="ti ti-alert-triangle"></i><span>В СДО лежит черновик — он не отправлен</span></div>` : "";
  const link = t.url ? `<button class="primary" data-link="${esc(t.url)}">${(LINK[t.source] || LINK.manual)[1].replace(" сообщение в группе", "")}</button>` : "";
  $("hero").innerHTML = `<section class="hero" data-id="${t.id}">
    <div class="hero-head"><span class="kicker">Ближайший дедлайн</span>${ptsTag(t)}</div>
    <div style="display:flex;flex-direction:column;gap:4px"><span class="hero-title">${esc(t.title)}</span>
      ${t.subject ? `<span class="hero-sub">${esc(t.subject)}</span>` : ""}</div>
    <div class="count"><b class="display">${timeLeft(d, now)}</b><span>${dueLabel(d, now)}</span></div>
    ${st}
    <div class="hero-actions">${link}<button class="${link ? "ghost" : "primary"}" data-done="${t.id}">Готово</button></div>
  </section>`;
}

function renderLate(late, now) {
  $("late").innerHTML = late.map((t) => `<div class="late" data-id="${t.id}">
    <span class="late-icon"><i class="ti ti-clock-exclamation"></i></span>
    <div class="late-body"><b>${esc(t.title)}</b><span>${lateLabel(deadlineOf(t), now)}${t.subject ? " · " + esc(t.subject) : ""}</span></div>
    ${chk(t)}</div>`).join("");
}

function renderList() {
  const now = new Date();
  const used = [...new Set(tasks.map((t) => t.subject).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru"));
  if (filter && !used.includes(filter)) filter = null;
  $("chips").hidden = used.length < 2;
  $("chips").innerHTML =
    `<button class="chip ${filter ? "" : "on"}" data-filter="">Все</button>` +
    used.map((s) => `<button class="chip ${filter === s ? "on" : ""}" data-filter="${esc(s)}"><span class="dot" style="background:${dotOf(s)}"></span>${esc(s)}</button>`).join("");

  const vis = tasks.filter((t) => !filter || t.subject === filter);
  const open = vis.filter((t) => !t.done);
  const upcoming = open.filter((t) => deadlineOf(t) && deadlineOf(t) >= now).sort(byDeadline);
  const late = open.filter((t) => deadlineOf(t) && deadlineOf(t) < now).sort(byDeadline);
  renderHeader(vis, open, upcoming, late, now);
  renderWeek(open, now);

  let html = "";
  if (dayFilter !== null) {
    renderHero(null);
    renderLate([], now);
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + dayFilter);
    const items = vis.filter((t) => deadlineOf(t) && dayDiff(deadlineOf(t), now) === dayFilter && (t.done || deadlineOf(t) >= now)).sort(byDeadline);
    html = group(cap(WD[d.getDay()]), dayMon(d), items, (t) => hm(deadlineOf(t)), dayFilter === 0)
      || `<div class="empty"><b>В этот день сроков нет</b><span>Нажми на день ещё раз, чтобы вернуть все задания.</span></div>`;
  } else {
    const hero = upcoming[0];
    renderHero(hero, now);
    renderLate(late, now);
    const rest = upcoming.filter((t) => t !== hero);
    for (let k = 0; k < 7; k++) {
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + k);
      const sub = k === 0 ? "сегодня" : k === 1 ? "завтра" : dayMon(d);
      html += group(cap(WD[d.getDay()]), sub, rest.filter((t) => dayDiff(deadlineOf(t), now) === k), (t) => hm(deadlineOf(t)), k === 0);
    }
    html += group("Позже", "", rest.filter((t) => dayDiff(deadlineOf(t), now) >= 7), (t) => dayMon(deadlineOf(t)));
    html += group("Без", "срока", open.filter((t) => !deadlineOf(t)), () => "");
    const done = vis.filter((t) => t.done).sort((a, b) => byDeadline(b, a));
    html += group("Готово", "", done, (t) => (deadlineOf(t) ? dayMon(deadlineOf(t)) : ""));
  }
  $("list").innerHTML = html;
  $("empty").hidden = dayFilter !== null || open.length > 0 || !$("error").hidden;
}

// ---------- карточка ----------

function openEdit(task) {
  current = task || {};
  const isNew = !current.id;
  const d = deadlineOf(current);
  $("edit-title").textContent = isNew ? "Новое задание" : "Задание";
  $("f-title").value = current.title || "";
  $("f-subject").value = current.subject || "";
  $("subjects").innerHTML = subjects.map((s) => `<option value="${esc(s)}">`).join("");
  $("f-date").value = d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}` : "";
  $("f-time").value = d ? hm(d) : "23:59";
  $("f-points").value = current.points ?? "";
  $("points-hint").textContent = current.source === "portal" && current.points != null && !current.edited ? "из СДО" : "";
  $("url-field").hidden = !!current.id && current.source !== "manual";
  $("f-url").value = current.source === "manual" ? current.url || "" : "";
  $("form-error").textContent = "";
  $("delete-btn").hidden = isNew;
  renderStatus();
  renderLink();

  $("list-screen").hidden = true;
  $("edit-screen").hidden = false;
  scrollTo(0, 0);
  if (inTg) {
    tgSafe(() => tg.BackButton.show());
    tgSafe(() => tg.MainButton.setParams({ text: isNew ? "Добавить" : "Сохранить", is_visible: true, is_active: true }));
    if (!hasBack()) $("dev-back").hidden = false; // в старом клиенте нет кнопки «Назад» Telegram
  } else {
    $("dev-back").hidden = false;
    $("save-btn").hidden = false;
    $("save-btn").textContent = isNew ? "Добавить" : "Сохранить";
  }
  if (isNew) $("f-title").focus();
}

function renderStatus() {
  const t = current;
  if (t.source !== "portal") { $("status-box").innerHTML = ""; return; }
  const st = statusOf(t) || statusOf({ ...t, status: "none" });
  let notes = "";
  if (t.auto_done && t.done) notes += `<div class="note ok"><i class="ti ti-circle-check"></i><span>Отмечено автоматически: задание сдано в СДО</span></div>`;
  if (t.status === "draft") notes += `<div class="note warn"><i class="ti ti-alert-triangle"></i><span>Файл загружен, но не отправлен. Нажми «Отправить на проверку» в СДО</span></div>`;
  notes += t.edited
    ? `<div class="note muted"><i class="ti ti-pencil"></i><span>Изменено вручную — обновления из СДО не перезаписывают твои правки</span></div>`
    : `<div class="note muted"><i class="ti ti-info-circle"></i><span>Если поменяешь срок или баллы, СДО их больше не перезапишет</span></div>`;
  $("status-box").innerHTML = `<div class="stbox"><div class="sthead"><span>Статус в СДО</span><span>${ago(t.checked_at)}</span></div>
    <div class="strow"><span>Ответ</span><b>${esc(st.ans)}</b></div>
    <div class="strow"><span>Оценивание</span><b>${esc(st.grade)}</b></div>${notes}</div>`;
}

function renderLink() {
  const t = current, a = $("open-link");
  if (!t.url) { a.hidden = true; return; }
  const [icon, text] = LINK[t.source] || LINK.manual;
  a.href = t.url;
  a.dataset.link = t.url;
  a.innerHTML = `<i class="ti ${icon}"></i><span>${text}</span><i class="ti ti-external-link"></i>`;
  a.hidden = false;
}

function closeEdit() {
  current = null;
  $("edit-screen").hidden = true;
  $("list-screen").hidden = false;
  if (inTg) { tgSafe(() => tg.BackButton.hide()); tgSafe(() => tg.MainButton.hide()); }
  renderList();
}

function onBack() {
  if (!$("theme-sheet").hidden) closeSheet();
  else closeEdit();
}

function formData() {
  const date = $("f-date").value, time = $("f-time").value || "23:59";
  const body = {
    title: $("f-title").value.trim(),
    subject: $("f-subject").value.trim() || null,
    deadline: date ? `${date}T${time}` : null,
    points: $("f-points").value.replace(",", ".") || null,
  };
  if (!current.id || current.source === "manual") body.url = $("f-url").value.trim() || null;
  return body;
}

async function save() {
  const body = formData();
  if (!body.title) { $("form-error").textContent = "Напиши, что нужно сделать"; $("f-title").focus(); return; }
  setBusy(true);
  try {
    const saved = current.id ? await api("PATCH", `/api/tasks/${current.id}`, body) : await api("POST", "/api/tasks", body);
    const i = tasks.findIndex((t) => t.id === saved.id);
    if (i >= 0) tasks[i] = saved; else tasks.push(saved);
    if (body.subject && !subjects.includes(body.subject)) subjects.push(body.subject);
    haptic("success");
    closeEdit();
  } catch (e) {
    $("form-error").textContent = e.message;
    haptic("error");
  } finally {
    setBusy(false);
  }
}

function setBusy(on) {
  if (inTg) tgSafe(() => (on ? tg.MainButton.showProgress() : tg.MainButton.hideProgress()));
  $("save-btn").disabled = on;
}

async function remove() {
  const ok = await confirmBox("Удалить задание? Отменить нельзя.");
  if (!ok) return;
  try {
    await api("DELETE", `/api/tasks/${current.id}`);
    tasks = tasks.filter((t) => t.id !== current.id);
    haptic("success");
    closeEdit();
  } catch (e) {
    $("form-error").textContent = e.message;
  }
}

function confirmBox(text) {
  if (inTg && tg.showConfirm && (!tg.isVersionAtLeast || tg.isVersionAtLeast("6.2"))) {
    return new Promise((resolve) => {
      try { tg.showConfirm(text, resolve); } catch (e) { resolve(window.confirm(text)); }
    });
  }
  return Promise.resolve(window.confirm(text));
}

async function toggleDone(id) {
  const t = tasks.find((x) => x.id === id);
  if (!t) return;
  t.done = !t.done; // сразу, не дожидаясь сервера
  haptic(t.done ? "success" : "light");
  renderList();
  try {
    Object.assign(t, await api("PATCH", `/api/tasks/${id}`, { done: t.done }));
  } catch (e) {
    t.done = !t.done;
    $("error").textContent = e.message;
    $("error").hidden = false;
  }
  renderList();
}

function haptic(kind) {
  if (!inTg || !tg.HapticFeedback) return;
  tgSafe(() => {
    if (kind === "light") tg.HapticFeedback.impactOccurred("light");
    else if (kind === "select") tg.HapticFeedback.selectionChanged();
    else tg.HapticFeedback.notificationOccurred(kind);
  });
}

function openLink(url) {
  if (!inTg) return false;
  try {
    if (/^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
    else tg.openLink(url);
    return true;
  } catch (e) {
    return false; // откроется обычной ссылкой
  }
}

// ---------- события ----------

document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-link]");
  if (link) {
    e.stopPropagation();
    if (openLink(link.dataset.link)) e.preventDefault(); // в Telegram ссылки открываем его средствами
    else if (link.tagName === "BUTTON") window.open(link.dataset.link, "_blank", "noopener");
    return;
  }
  const done = e.target.closest("[data-done]");
  if (done) return toggleDone(Number(done.dataset.done));
  const pal = e.target.closest("[data-pal]"); // не data-palette: он стоит на <html>
  if (pal) return setTheme({ palette: pal.dataset.pal });
  const mode = e.target.closest("[data-mode]");
  if (mode) return setTheme({ mode: mode.dataset.mode });
  if (e.target === $("theme-sheet")) return closeSheet(); // тап мимо листа
  const day = e.target.closest("[data-day]");
  if (day) {
    const i = Number(day.dataset.day);
    dayFilter = dayFilter === i ? null : i;
    haptic("select");
    return renderList();
  }
  const f = e.target.closest("[data-filter]");
  if (f) { filter = f.dataset.filter || null; store(FILTER_KEY, filter || ""); return renderList(); }
  const item = e.target.closest("#list-screen [data-id]");
  if (item) openEdit(tasks.find((t) => t.id === Number(item.dataset.id)));
});
$("new-btn").addEventListener("click", () => openEdit(null));
$("theme-btn").addEventListener("click", openSheet);
$("theme-close").addEventListener("click", closeSheet);
$("save-btn").addEventListener("click", save);
$("back-btn").addEventListener("click", closeEdit);
$("delete-btn").addEventListener("click", remove);
$("form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
$("f-title").addEventListener("input", () => { $("form-error").textContent = ""; });

function showFatal(msg) {
  $("error").textContent = `Приложение не запустилось: ${msg}. Обнови Telegram или открой бота с телефона.`;
  $("error").hidden = false;
}
// Любая ошибка — текстом на экране, а не пустой заготовкой страницы
window.addEventListener("error", (e) => showFatal(e.message));
window.addEventListener("unhandledrejection", (e) => showFatal((e.reason && e.reason.message) || String(e.reason)));

async function start() {
  try { theme = { ...theme, ...JSON.parse(localStorage.getItem(THEME_KEY) || "{}") }; } catch (e) { /* нет — по умолчанию */ }
  try { filter = localStorage.getItem(FILTER_KEY) || null; } catch (e) { /* нет — все предметы */ }
  applyTheme();
  if (inTg) {
    tgSafe(() => tg.ready());
    tgSafe(() => tg.expand());
    tgSafe(() => tg.onEvent("themeChanged", applyTheme));
    tgSafe(() => tg.BackButton.onClick(onBack));
    tgSafe(() => tg.MainButton.onClick(save));
  } else {
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  }
  // Сначала задания, потом настройки из облака — облако не должно задерживать список
  await load();
  renderList();
  try { theme = { ...theme, ...JSON.parse((await restore(THEME_KEY)) || "{}") }; } catch (e) { /* битое значение */ }
  applyTheme();
  filter = (await restore(FILTER_KEY)) || filter;
  renderList();
  // Кнопка «Открыть» под уведомлением ведёт сразу в карточку: ?task=<id>
  const wanted = Number(new URLSearchParams(location.search).get("task"));
  const t = wanted && tasks.find((x) => x.id === wanted);
  if (t) openEdit(t);
  // Вернулся в приложение — подтянуть свежие данные (бот мог закрыть задания по СДО)
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState === "visible" && !current) { await load(); renderList(); }
  });
  // Обратный отсчёт в «Ближайшем дедлайне»
  setInterval(() => { if (!current && document.visibilityState === "visible") renderList(); }, 60000);
}

start();
