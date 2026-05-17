const state = {
  token: localStorage.getItem("ege_token"),
  user: null,
  bootstrap: null,
  mode: "rule",
  selectedCategory: null,
  selectedRuleId: null,
  currentSession: null,
  answers: {},
  startedAt: null,
};

const modes = {
  rule: {
    title: "Правило",
    hint: "Большая группа и подвыбор внутри нее",
    eyebrow: "точечная отработка",
  },
  mix: {
    title: "Микс",
    hint: "Разные правила в формате слово = буква",
    eyebrow: "перемешанные орфограммы",
  },
  line: {
    title: "Строка",
    hint: "Ряд с одной и той же буквой",
    eyebrow: "формат задания 9",
  },
};

const view = document.querySelector("#view");
const topActions = document.querySelector("#topActions");

function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return fetch(path, { ...options, headers }).then(async (response) => {
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Ошибка запроса");
    return data;
  });
}

function pct(correct, total) {
  if (!total) return "0%";
  return `${Math.round((correct / total) * 100)}%`;
}

function ruleCategories() {
  return Object.keys(state.bootstrap.rules);
}

function selectedRules() {
  return state.bootstrap.rules[state.selectedCategory] || [];
}

function ensureRuleSelection() {
  const categories = ruleCategories();
  if (!state.selectedCategory || !state.bootstrap.rules[state.selectedCategory]) {
    state.selectedCategory = categories[0] || null;
  }
  const rules = selectedRules();
  if (!rules.some((rule) => rule.rule_id === state.selectedRuleId)) {
    state.selectedRuleId = rules[0]?.rule_id || null;
  }
}

function renderTopActions() {
  topActions.innerHTML = "";
  if (!state.user) return;
  const role = document.createElement("span");
  role.className = "muted";
  role.textContent = `${state.user.display_name} · ${state.user.role === "teacher" ? "учитель" : "ученик"}`;
  const logout = document.createElement("button");
  logout.className = "ghost-button";
  logout.textContent = "Выйти";
  logout.addEventListener("click", async () => {
    await api("/api/logout", { method: "POST", body: "{}" }).catch(() => null);
    localStorage.removeItem("ege_token");
    state.token = null;
    state.user = null;
    renderLogin();
  });
  topActions.append(role, logout);
}

function renderLogin() {
  renderTopActions();
  const template = document.querySelector("#loginTemplate").content.cloneNode(true);
  view.replaceChildren(template);
  document.querySelector("#loginForm").insertAdjacentHTML("afterend", `
    <form class="login-panel register-panel" id="registerForm">
      <h2>Регистрация</h2>
      <label>
        Имя
        <input name="display_name" autocomplete="name" />
      </label>
      <label>
        Логин
        <input name="username" autocomplete="username" />
      </label>
      <label>
        Пароль
        <input name="password" type="password" autocomplete="new-password" />
      </label>
      <label>
        Роль
        <select name="role" id="registerRole">
          <option value="student">Ученик</option>
          <option value="teacher">Учитель</option>
        </select>
      </label>
      <label id="teacherCodeLabel">
        Код учителя
        <input name="teacher_code" placeholder="например, TEACHER-2026" />
      </label>
      <button class="secondary-button" type="submit">Создать аккаунт</button>
      <p class="muted">Ученики регистрируются только по коду учителя.</p>
      <p class="error" id="registerError"></p>
    </form>
  `);
  document.querySelector("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const error = document.querySelector("#loginError");
    error.textContent = "";
    try {
      const data = await api("/api/login", {
        method: "POST",
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
        }),
      });
      state.token = data.token;
      state.user = data.user;
      localStorage.setItem("ege_token", data.token);
      await loadBootstrap();
      renderDashboard();
    } catch (err) {
      error.textContent = err.message;
    }
  });
  const roleSelect = document.querySelector("#registerRole");
  const codeLabel = document.querySelector("#teacherCodeLabel");
  roleSelect.addEventListener("change", () => {
    codeLabel.classList.toggle("hidden", roleSelect.value === "teacher");
  });
  document.querySelector("#registerForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const error = document.querySelector("#registerError");
    error.textContent = "";
    try {
      const data = await api("/api/register", {
        method: "POST",
        body: JSON.stringify({
          display_name: form.get("display_name"),
          username: form.get("username"),
          password: form.get("password"),
          role: form.get("role"),
          teacher_code: form.get("teacher_code"),
        }),
      });
      state.token = data.token;
      state.user = data.user;
      localStorage.setItem("ege_token", data.token);
      await loadBootstrap();
      renderDashboard();
    } catch (err) {
      error.textContent = err.message;
    }
  });
}

async function loadBootstrap() {
  state.bootstrap = await api("/api/bootstrap");
}

async function restoreSession() {
  await loadBootstrap();
  if (!state.token) {
    renderLogin();
    return;
  }
  const data = await api("/api/me").catch(() => ({ user: null }));
  state.user = data.user;
  if (!state.user) {
    localStorage.removeItem("ege_token");
    state.token = null;
    renderLogin();
    return;
  }
  renderDashboard();
}

function renderDashboard() {
  renderTopActions();
  ensureRuleSelection();
  const template = document.querySelector("#dashboardTemplate").content.cloneNode(true);
  view.replaceChildren(template);
  renderSidebar();
  renderMode();
  document.querySelector("#progressButton").addEventListener("click", showProgress);
}

function renderSidebar() {
  const teacherCode = state.user.role === "teacher" && state.user.teacher_code
    ? `<span class="muted">Код для учеников: <b>${state.user.teacher_code}</b></span>`
    : "";
  document.querySelector("#userBlock").innerHTML = `
    <strong>${state.user.display_name}</strong>
    <span class="muted">${state.user.role === "teacher" ? "Кабинет учителя" : "Кабинет ученика"}</span>
    ${teacherCode}
  `;
  const list = document.querySelector("#modeList");
  list.innerHTML = "";
  Object.entries(modes).forEach(([modeId, mode]) => {
    const button = document.createElement("button");
    button.className = `mode-button ${state.mode === modeId ? "active" : ""}`;
    button.innerHTML = `<b>${mode.title}</b><span>${mode.hint}</span>`;
    button.addEventListener("click", () => {
      state.mode = modeId;
      state.currentSession = null;
      state.answers = {};
      renderSidebar();
      renderMode();
    });
    list.append(button);
  });
  document.querySelector("#quickStats").innerHTML = `
    <div class="stat"><b>${state.bootstrap.word_count}</b><span>слов в базе</span></div>
    <div class="stat"><b>${Object.values(state.bootstrap.rules).flat().length}</b><span>подправила</span></div>
  `;
}

function renderMode() {
  const mode = modes[state.mode];
  document.querySelector("#modeEyebrow").textContent = mode.eyebrow;
  document.querySelector("#modeTitle").textContent = mode.title;
  document.querySelector("#practiceView").classList.add("hidden");
  document.querySelector("#resultView").classList.add("hidden");
  renderSetup();
}

function renderSetup() {
  const setup = document.querySelector("#setupView");
  const ruleSelector = state.mode === "rule" ? renderRuleSelector() : "";
  setup.innerHTML = `
    <div class="setup-grid">
      <label>
        Количество вопросов
        <select id="questionCount">
          <option value="10">10</option>
          <option value="15">15</option>
          <option value="20">20</option>
          <option value="30">30</option>
        </select>
      </label>
      <button class="primary-button" id="startPractice" type="button">Начать</button>
    </div>
    ${ruleSelector}
  `;
  setup.querySelector("#startPractice").addEventListener("click", startPractice);
  setup.querySelectorAll("[data-category]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedCategory = button.dataset.category;
      state.selectedRuleId = null;
      ensureRuleSelection();
      renderSetup();
    });
  });
  setup.querySelector("#ruleSelect")?.addEventListener("change", (event) => {
    state.selectedRuleId = event.target.value;
    renderSetup();
  });
}

function renderRuleSelector() {
  ensureRuleSelection();
  const categoryButtons = ruleCategories()
    .map((category) => `
      <button class="category-pill ${category === state.selectedCategory ? "active" : ""}" data-category="${category}" type="button">
        <span>${category}</span>
        <b>${state.bootstrap.rules[category].reduce((sum, rule) => sum + rule.count, 0)}</b>
      </button>
    `)
    .join("");
  const ruleOptions = selectedRules()
    .map((rule) => `<option value="${rule.rule_id}" ${rule.rule_id === state.selectedRuleId ? "selected" : ""}>${rule.rule_name} · ${rule.count}</option>`)
    .join("");
  const activeRule = selectedRules().find((rule) => rule.rule_id === state.selectedRuleId);
  return `
    <section class="rule-picker">
      <div class="category-grid">${categoryButtons}</div>
      <div class="rule-select-row">
        <label>
          Подвыбор внутри группы
          <select id="ruleSelect">${ruleOptions}</select>
        </label>
        <div class="selected-rule">
          <b>${activeRule?.count || 0}</b>
          <span>слов в диапазоне</span>
        </div>
      </div>
    </section>
  `;
}

async function startPractice() {
  const count = Number(document.querySelector("#questionCount").value);
  const payload = { mode: state.mode, count };
  if (state.mode === "rule") payload.rule_id = state.selectedRuleId;
  const setup = document.querySelector("#setupView");
  try {
    const data = await api("/api/practice/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.currentSession = data;
    state.answers = {};
    state.startedAt = Date.now();
    setup.innerHTML = "";
    renderQuestions();
  } catch (err) {
    setup.insertAdjacentHTML("beforeend", `<p class="error">${err.message}</p>`);
  }
}

function renderQuestions() {
  const practice = document.querySelector("#practiceView");
  practice.classList.remove("hidden");
  practice.innerHTML = `
    <div class="question-stack">
      ${state.currentSession.questions.map(renderQuestion).join("")}
    </div>
    <div class="practice-actions">
      <button class="ghost-button" id="cancelPractice" type="button">Сбросить</button>
      <button class="primary-button" id="submitPractice" type="button">Проверить</button>
    </div>
  `;
  practice.querySelector("#cancelPractice").addEventListener("click", renderMode);
  practice.querySelector("#submitPractice").addEventListener("click", submitPractice);
  practice.querySelectorAll("[data-answer]").forEach((button) => {
    button.addEventListener("click", () => {
      if (button.dataset.multi === "true") {
        const current = new Set((state.answers[button.dataset.questionId] || "").split("").filter(Boolean));
        if (current.has(button.dataset.answer)) {
          current.delete(button.dataset.answer);
        } else {
          current.add(button.dataset.answer);
        }
        state.answers[button.dataset.questionId] = [...current].sort().join("");
      } else {
        state.answers[button.dataset.questionId] = button.dataset.answer;
      }
      renderQuestions();
    });
  });
}

function renderQuestion(question, index) {
  if (question.kind === "line") {
    const rows = question.rows
      .map((row, rowIndex) => {
        const answer = String(rowIndex + 1);
        const selected = (state.answers[question.question_id] || "").includes(answer);
        return `
          <button class="line-row ${selected ? "selected" : ""}" data-multi="true" data-question-id="${question.question_id}" data-answer="${answer}" type="button">
            <b>${answer}</b>
            <span class="line-words">${row.map((word) => `<span>${word}</span>`).join("")}</span>
          </button>
        `;
      })
      .join("");
    return `
      <article class="question">
        <div class="question-head"><span>Вопрос ${index + 1}</span><span>${question.rule_name}</span></div>
        <div>${question.prompt}</div>
        ${rows}
      </article>
    `;
  }
  const choices = question.choices
    .map((choice) => {
      const selected = state.answers[question.question_id] === choice;
      return `<button class="choice ${selected ? "selected" : ""}" data-question-id="${question.question_id}" data-answer="${choice}" type="button">${choice}</button>`;
    })
    .join("");
  return `
    <article class="question">
      <div class="question-head"><span>Вопрос ${index + 1}</span><span>${question.rule_name}</span></div>
      <div class="word-prompt">${question.prompt}</div>
      <div class="choice-row">${choices}</div>
    </article>
  `;
}

async function submitPractice() {
  const total = state.currentSession.questions.length;
  if (Object.keys(state.answers).length < total) {
    alert("Ответьте на все вопросы перед проверкой.");
    return;
  }
  const elapsed = Math.round((Date.now() - state.startedAt) / 1000);
  const data = await api("/api/practice/submit", {
    method: "POST",
    body: JSON.stringify({
      session_id: state.currentSession.session_id,
      answers: state.answers,
      time_spent_sec: elapsed,
    }),
  });
  renderResults(data);
}

function renderResults(data) {
  document.querySelector("#practiceView").classList.add("hidden");
  const result = document.querySelector("#resultView");
  result.classList.remove("hidden");
  result.innerHTML = `
    <div class="stat"><b>${data.correct}/${data.total}</b><span>${pct(data.correct, data.total)} правильных ответов</span></div>
    <div class="result-list">
      ${data.results.map((item, index) => `
        <div class="result-item ${item.is_correct ? "ok" : "bad"}">
          <b>${index + 1}. ${item.is_correct ? "Верно" : "Повторим еще"}</b>
          <p>Ответ: ${item.given_answer || "—"} · правильно: ${item.correct_answer}</p>
          <p>${item.correct_spelling || ""}</p>
          <p class="muted">${item.explanation || ""}</p>
        </div>
      `).join("")}
    </div>
    <div class="practice-actions">
      <button class="primary-button" type="button" id="againButton">Новая тренировка</button>
    </div>
  `;
  result.querySelector("#againButton").addEventListener("click", renderMode);
}

async function showProgress() {
  const data = await api("/api/progress");
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  const summary = data.summary;
  const studentRows = data.by_student.map((row) => `
    <tr><td>${row.display_name}</td><td>${row.total}</td><td>${pct(row.correct, row.total)}</td></tr>
  `).join("");
  const ruleRows = data.by_rule.map((row) => `
    <tr><td>${row.rule_name}</td><td>${row.total}</td><td>${pct(row.correct, row.total)}</td></tr>
  `).join("");
  const recentRows = data.recent.map((row) => `
    <tr>
      <td>${new Date(row.created_at).toLocaleString()}</td>
      <td>${row.display_name}</td>
      <td>${row.prompt}</td>
      <td>${row.given_answer} / ${row.correct_answer}</td>
      <td>${row.is_correct ? "да" : "нет"}</td>
    </tr>
  `).join("");
  backdrop.innerHTML = `
    <section class="progress-modal">
      <div class="panel-head">
        <div><p class="eyebrow">прогресс</p><h2>${state.user.role === "teacher" ? "Журнал класса" : "Мои результаты"}</h2></div>
        <button class="secondary-button" id="closeProgress" type="button">Закрыть</button>
      </div>
      <div class="progress-grid">
        <div class="stat"><b>${summary.total}</b><span>ответов</span></div>
        <div class="stat"><b>${summary.correct}</b><span>верно</span></div>
        <div class="stat"><b>${pct(summary.correct, summary.total)}</b><span>точность</span></div>
      </div>
      ${state.user.role !== "teacher" ? `<div class="stat"><b>${data.due_reviews}</b><span>слов в очереди повторения</span></div>` : ""}
      ${state.user.role === "teacher" ? `<h3>Ученики</h3><table class="table"><tr><th>Имя</th><th>Ответов</th><th>Точность</th></tr>${studentRows}</table>` : ""}
      <h3>Правила</h3>
      <table class="table"><tr><th>Правило</th><th>Ответов</th><th>Точность</th></tr>${ruleRows}</table>
      <h3>Последние попытки</h3>
      <table class="table"><tr><th>Дата</th><th>Пользователь</th><th>Задание</th><th>Ответ</th><th>Верно</th></tr>${recentRows}</table>
    </section>
  `;
  document.body.append(backdrop);
  backdrop.querySelector("#closeProgress").addEventListener("click", () => backdrop.remove());
}

restoreSession();
