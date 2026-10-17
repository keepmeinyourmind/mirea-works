// StudyBot Mini App: список заданий и карточка. Данные — через API бота (/api/tasks).
"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
const inTg = !!(tg && tg.initData);
const $ = (id) => document.getElementById(id);

const SECS = ["Просрочено", "Сегодня", "Завтра", "На неделе", "Позже", "Без срока", "Сделано"];
const WEEK = ["Сегодня", "Завтра", "На неделе"];
const WD = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const SRC = { portal: "СДО", tg: "чат", manual: "вручную" };
const LINK = {
  portal: ["ti-school", "Открыть в СДО"],
  tg: ["ti-message", "Открыть сообщение в группе"],
  manual: ["ti-link", "Открыть ссылку"],
};
const FILTER_KEY = "subject_filter";

let tasks = [];
let subjects = [];
let filter = null;
let current = null; // задание в карточке; {} — новое

// ---------- оформление ----------

function applyTheme() {
  const dark = inTg ? tg.colorScheme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.classList.toggle("dark", dark);
}

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtNum = (n) => (Number.isInteger(n) ? String(n) : String(n).replace(".", ","));
function plural(n) {
  n = Math.floor(n);
  const a = n % 10, b = n % 100;
  return a === 1 && b !== 11 ? "балл" : a >= 2 && a <= 4 && (b < 12 || b > 14) ? "балла" : "баллов";
}
function colorOf(s) {
  let h = 0;
  for (const ch of s) h = (h * 31 + ch.codePointAt(0)) >>> 0;
  return "c" + (h % 6);
}
const pill = (s) => (s ? `<span class="pill ${colorOf(s)}">${esc(s)}</span>` : "");

// ---------- сроки и секции ----------

const deadlineOf = (t) => (t.deadline ? new Date(t.deadline) : null);
function sectionOf(t) {
  if (t.done) return "Сделано";
  const d = deadlineOf(t);
  if (!d) return "Без срока";
  const now = new Date();
  if (d < now) return "Просрочено";
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const days = Math.round((day(d) - day(now)) / 864e5);
  return days === 0 ? "Сегодня" : days === 1 ? "Завтра" : days <= 7 ? "На неделе" : "Позже";
}
function deadlineLabel(t) {
  const d = deadlineOf(t);
  if (!d) return "без срока";
  const hm = d.toTimeString().slice(0, 5);
  const dm = `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
  return `до ${dm} (${WD[d.getDay()]})${hm !== "23:59" ? ", " + hm : ""}`;
}

// ---------- статус из СДО ----------

function statusOf(t) {
  if (t.source !== "portal" || !t.status) return null;
  const pts = t.points != null ? fmtNum(t.points) : "?";
  switch (t.status) {
    case "draft": return { cls: "st-warn", short: "Черновик, не отправлено", ans: "Черновик (не отправлен)", grade: "Не оценено" };
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

// ---------- хранение фильтра ----------

function saveFilter() {
  const v = filter || "";
  if (inTg && tg.CloudStorage) tg.CloudStorage.setItem(FILTER_KEY, v, () => {});
  else try { localStorage.setItem(FILTER_KEY, v); } catch (e) { /* приватный режим — не страшно */ }
}
function loadFilter() {
  return new Promise((resolve) => {
    if (inTg && tg.CloudStorage) tg.CloudStorage.getItem(FILTER_KEY, (err, v) => resolve(err ? null : v || null));
    else { try { resolve(localStorage.getItem(FILTER_KEY) || null); } catch (e) { resolve(null); } }
  });
}

// ---------- список ----------

function renderList() {
  const used = [...new Set(tasks.map((t) => t.subject).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ru"));
  if (filter && !used.includes(filter)) filter = null;
  $("chips").innerHTML =
    `<button class="pill all ${filter ? "" : "on"}" data-filter="">Все</button>` +
    used.map((s) => `<button class="pill ${colorOf(s)} ${filter && filter !== s ? "off" : ""}" data-filter="${esc(s)}">${esc(s)}</button>`).join("");

  const vis = tasks.filter((t) => !filter || t.subject === filter);
  const week = vis.filter((t) => WEEK.includes(sectionOf(t))).reduce((a, t) => a + (t.points || 0), 0);
  $("sum").hidden = week === 0;
  $("sum").innerHTML = `<span>Можно набрать на этой неделе</span><span class="pts">${fmtNum(Math.round(week * 10) / 10)} ${plural(week)}</span>`;

  let html = "";
  for (const sec of SECS) {
    const items = vis.filter((t) => sectionOf(t) === sec).sort((a, b) => (deadlineOf(a) || 0) - (deadlineOf(b) || 0));
    if (!items.length) continue;
    const late = sec === "Просрочено";
    html += `<div class="sec ${late ? "red" : ""}"><span>${sec}</span><span>${items.filter((t) => !t.done).length || ""}</span></div>`;
    for (const t of items) {
      const st = statusOf(t);
      html += `<div class="row ${t.done ? "done" : ""}" data-id="${t.id}">
        <button class="chk ${t.done ? "on" : ""}" data-done="${t.id}" aria-label="${t.done ? "Вернуть в работу" : "Отметить сделанным"}"><i class="ti ti-check"></i></button>
        <div class="body"><div class="title">${esc(t.title)}</div>
          <div class="meta">${pill(t.subject)}<span class="${late && !t.done ? "late" : ""}">${deadlineLabel(t)} · ${SRC[t.source] || ""}</span>${st && st.short ? `<span class="st ${st.cls}">${st.short}</span>` : ""}</div></div>
        ${t.url ? `<a class="lnk" href="${esc(t.url)}" data-link="${esc(t.url)}" aria-label="${LINK[t.source][1]}"><i class="ti ti-external-link"></i></a>` : ""}
        ${t.points ? `<span class="pts">+${fmtNum(t.points)} б.</span>` : ""}
        <i class="ti ti-chevron-right chev"></i></div>`;
    }
  }
  $("list").innerHTML = html;
  $("empty").hidden = !!vis.some((t) => !t.done) || !$("error").hidden;
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
  $("f-time").value = d ? d.toTimeString().slice(0, 5) : "23:59";
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
    tg.BackButton.show();
    tg.MainButton.setParams({ text: isNew ? "Добавить" : "Сохранить", is_visible: true, is_active: true });
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
  if (inTg) { tg.BackButton.hide(); tg.MainButton.hide(); }
  renderList();
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
  if (inTg) on ? tg.MainButton.showProgress() : tg.MainButton.hideProgress();
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
  if (inTg && tg.showConfirm) return new Promise((resolve) => tg.showConfirm(text, resolve));
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
  if (kind === "light") tg.HapticFeedback.impactOccurred("light");
  else tg.HapticFeedback.notificationOccurred(kind);
}

function openLink(url) {
  if (!inTg) return false;
  if (/^https:\/\/t\.me\//.test(url)) tg.openTelegramLink(url);
  else tg.openLink(url);
  return true;
}

// ---------- события ----------

document.addEventListener("click", (e) => {
  const link = e.target.closest("[data-link]");
  if (link) {
    e.stopPropagation();
    if (openLink(link.dataset.link)) e.preventDefault(); // в Telegram ссылки открываем его средствами
    return;
  }
  const chk = e.target.closest("[data-done]");
  if (chk) return toggleDone(Number(chk.dataset.done));
  const f = e.target.closest("[data-filter]");
  if (f) { filter = f.dataset.filter || null; saveFilter(); return renderList(); }
  const row = e.target.closest(".row[data-id]");
  if (row) openEdit(tasks.find((t) => t.id === Number(row.dataset.id)));
});
$("new-btn").addEventListener("click", () => openEdit(null));
$("save-btn").addEventListener("click", save);
$("back-btn").addEventListener("click", closeEdit);
$("delete-btn").addEventListener("click", remove);
$("form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
$("f-title").addEventListener("input", () => { $("form-error").textContent = ""; });

async function start() {
  applyTheme();
  if (inTg) {
    tg.ready();
    tg.expand();
    tg.onEvent("themeChanged", applyTheme);
    tg.BackButton.onClick(closeEdit);
    tg.MainButton.onClick(save);
  } else {
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  }
  filter = await loadFilter();
  await load();
  renderList();
  // Кнопка «Открыть» под уведомлением ведёт сразу в карточку: ?task=<id>
  const wanted = Number(new URLSearchParams(location.search).get("task"));
  const t = wanted && tasks.find((x) => x.id === wanted);
  if (t) openEdit(t);
  // Вернулся в приложение — подтянуть свежие данные (бот мог закрыть задания по СДО)
  document.addEventListener("visibilitychange", async () => {
    if (document.visibilityState === "visible" && !current) { await load(); renderList(); }
  });
}

start();
