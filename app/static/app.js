const state = {
  token: localStorage.getItem("ege_token"),
  user: null,
  bootstrap: null,
  mode: "rule",
  selectedCategory: null,
  selectedRuleIds: [],
  currentSession: null,
  answers: {},
  startedAt: null,
  questionCount: 10,
  manualInput: false,
  currentQuestionIndex: 0,
  liveResults: [],
};

const modes = {
  rule: {
    title: "Правило",
    hint: "Большая группа и подвыбор внутри нее",
    eyebrow: "точечная отработка",
  },
  word_letter: {
    title: "Слово - буква",
    hint: "Одно слово на экране, ввод буквы и мгновенная проверка",
    eyebrow: "быстрая отработка",
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
  errors: {
    title: "Копилка ошибок",
    hint: "Слова, где уже были промахи",
    eyebrow: "личное повторение",
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

async function downloadRequest(path, filename, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) headers["Content-Type"] = "application/json";
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ error: "Ошибка скачивания" }));
    throw new Error(data.error || "Ошибка скачивания");
  }
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
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

function selectedRuleSet() {
  return new Set(state.selectedRuleIds);
}

function ensureRuleSelection() {
  const categories = ruleCategories();
  if (!state.selectedCategory || !state.bootstrap.rules[state.selectedCategory]) {
    state.selectedCategory = categories[0] || null;
  }
  const rules = selectedRules();
  const available = new Set(rules.map((rule) => rule.rule_id));
  state.selectedRuleIds = state.selectedRuleIds.filter((ruleId) => available.has(ruleId));
  if (!state.selectedRuleIds.length && rules.length) {
    state.selectedRuleIds = rules.map((rule) => rule.rule_id);
  }
}

function renderTopActions() {
  topActions.innerHTML = "";
  if (!state.user) return;
  const role = document.createElement("span");
  role.className = "muted";
  const roleNames = { admin: "администратор", teacher: "учитель", student: "ученик" };
  role.textContent = `${state.user.display_name} · ${roleNames[state.user.role] || state.user.role}`;
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
  topActions.append(role);
  if (state.user.role === "admin") {
    const admin = document.createElement("button");
    admin.className = "ghost-button";
    admin.textContent = "Админ";
    admin.addEventListener("click", showAdmin);
    topActions.append(admin);
  }
  topActions.append(logout);
}

function renderLogin() {
  renderTopActions();
  const template = document.querySelector("#loginTemplate").content.cloneNode(true);
  view.replaceChildren(template);
  document.querySelector("#loginForm").insertAdjacentHTML("afterend", `
    <form class="login-panel register-panel" id="registerForm">
      <h2>Регистрация</h2>
      <div class="role-choice" aria-label="Роль">
        <label>
          <input type="radio" name="role" value="student" checked />
          <span>Ученик</span>
        </label>
        <label>
          <input type="radio" name="role" value="teacher" />
          <span>Учитель</span>
        </label>
      </div>
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
      <label id="teacherCodeLabel">
        Код учителя
        <input name="teacher_code" placeholder="например, TEACHER-2026" />
      </label>
      <button class="secondary-button" type="submit">Создать аккаунт</button>
      <p class="muted">Ученики регистрируются только по коду учителя.</p>
      <p class="muted warning-note">Запишите пароль и логин: платформа не собирает ПД, поэтому восстановление пароля будет невозможным в случае утери.</p>
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
  const codeLabel = document.querySelector("#teacherCodeLabel");
  document.querySelectorAll("input[name='role']").forEach((input) => {
    input.addEventListener("change", () => {
      const role = document.querySelector("input[name='role']:checked").value;
      codeLabel.classList.toggle("hidden", role === "teacher");
    });
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
  if (state.user.role === "admin") {
    renderAdminDashboard();
    return;
  }
  ensureRuleSelection();
  const template = document.querySelector("#dashboardTemplate").content.cloneNode(true);
  view.replaceChildren(template);
  renderSidebar();
  renderMode();
  document.querySelector("#progressButton").addEventListener("click", showProgress);
  if (state.user.role === "teacher") {
    renderTeacherDashboardPreview();
  }
}

function renderSidebar() {
  const teacherCode = state.user.role === "teacher" && state.user.teacher_code
    ? `<span class="muted">Код для учеников: <b>${state.user.teacher_code}</b></span>`
    : "";
  document.querySelector("#userBlock").innerHTML = `
    <strong>${state.user.display_name}</strong>
    <span class="muted">${state.user.role === "admin" ? "Кабинет администратора" : state.user.role === "teacher" ? "Кабинет учителя" : "Кабинет ученика"}</span>
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
  const ruleSelector = ["rule", "word_letter"].includes(state.mode) ? renderRuleSelector() : "";
  setup.innerHTML = `
    <div class="setup-grid">
      <label>
        Количество вопросов: <b id="questionCountValue">${state.questionCount}</b>
        <input id="questionCount" type="range" min="1" max="30" value="${state.questionCount}" />
      </label>
      <button class="primary-button" id="startPractice" type="button">Начать</button>
    </div>
    <label class="manual-toggle">
      <input id="manualInput" type="checkbox" ${state.manualInput || state.mode === "word_letter" ? "checked" : ""} ${state.mode === "word_letter" ? "disabled" : ""} />
      <span>Самостоятельно вводить ответ с клавиатуры</span>
    </label>
    ${ruleSelector}
  `;
  setup.querySelector("#startPractice").addEventListener("click", startPractice);
  setup.querySelector("#questionCount").addEventListener("input", (event) => {
    state.questionCount = Number(event.target.value);
    setup.querySelector("#questionCountValue").textContent = state.questionCount;
  });
  setup.querySelector("#manualInput").addEventListener("change", (event) => {
    state.manualInput = event.target.checked;
  });
  setup.querySelectorAll("[data-category]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedCategory = button.dataset.category;
      state.selectedRuleIds = [];
      ensureRuleSelection();
      renderSetup();
    });
  });
  setup.querySelector("#allRules")?.addEventListener("change", (event) => {
    state.selectedRuleIds = event.target.checked ? selectedRules().map((rule) => rule.rule_id) : [];
    renderSetup();
  });
  setup.querySelectorAll("[data-rule-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const next = selectedRuleSet();
      if (checkbox.checked) {
        next.add(checkbox.dataset.ruleId);
      } else {
        next.delete(checkbox.dataset.ruleId);
      }
      state.selectedRuleIds = [...next];
      renderSetup();
    });
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
  const selected = selectedRuleSet();
  const rules = selectedRules();
  const allSelected = rules.length > 0 && selected.size === rules.length;
  const selectedCount = rules
    .filter((rule) => selected.has(rule.rule_id))
    .reduce((sum, rule) => sum + rule.count, 0);
  const ruleOptions = rules
    .map((rule) => `
      <label class="rule-check">
        <input type="checkbox" data-rule-id="${rule.rule_id}" ${selected.has(rule.rule_id) ? "checked" : ""} />
        <span>${rule.rule_name}</span>
        <b>${rule.count}</b>
      </label>
    `)
    .join("");
  return `
    <section class="rule-picker">
      <div class="category-grid">${categoryButtons}</div>
      <div class="rule-select-row">
        <div class="rule-check-list">
          <label class="rule-check rule-check-all">
            <input id="allRules" type="checkbox" ${allSelected ? "checked" : ""} />
            <span>Все подгруппы внутри орфограммы</span>
            <b>${rules.reduce((sum, rule) => sum + rule.count, 0)}</b>
          </label>
          ${ruleOptions}
        </div>
        <div class="selected-rule">
          <b>${selectedCount}</b>
          <span>слов в выбранных подгруппах</span>
        </div>
      </div>
    </section>
  `;
}

async function startPractice() {
  const count = state.questionCount;
  const payload = { mode: state.mode, count };
  if (["rule", "word_letter"].includes(state.mode)) payload.rule_ids = state.selectedRuleIds;
  const setup = document.querySelector("#setupView");
  try {
    const data = await api("/api/practice/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.currentSession = data;
    state.answers = {};
    state.liveResults = [];
    state.currentQuestionIndex = 0;
    state.startedAt = Date.now();
    setup.innerHTML = "";
    if (state.mode === "word_letter") {
      renderLiveQuestion();
    } else {
      renderQuestions();
    }
  } catch (err) {
    setup.insertAdjacentHTML("beforeend", `<p class="error">${err.message}</p>`);
  }
}

function normalizeLetter(value) {
  return String(value || "").trim().toLowerCase().replace("ё", "ё").slice(0, 1);
}

function renderLiveQuestion(feedback = null) {
  const practice = document.querySelector("#practiceView");
  const result = document.querySelector("#resultView");
  result.classList.add("hidden");
  practice.classList.remove("hidden");
  const question = state.currentSession.questions[state.currentQuestionIndex];
  if (!question) {
    renderResults({
      results: state.liveResults,
      correct: state.liveResults.filter((item) => item.is_correct).length,
      total: state.liveResults.length,
    });
    return;
  }
  practice.innerHTML = `
    <article class="question live-question">
      <div class="question-head">
        <span>Слово ${state.currentQuestionIndex + 1} из ${state.currentSession.questions.length}</span>
        <span>${question.rule_name}</span>
      </div>
      <div class="word-prompt">${question.prompt}</div>
      <div class="letter-input-row">
        <input id="liveAnswer" maxlength="1" autocomplete="off" inputmode="text" aria-label="Введите букву" />
        <button class="primary-button" id="checkLiveAnswer" type="button">Проверить</button>
      </div>
      <div id="liveFeedback">${feedback || ""}</div>
    </article>
    <div class="practice-actions">
      <button class="ghost-button" id="cancelPractice" type="button">Сбросить</button>
    </div>
  `;
  const input = practice.querySelector("#liveAnswer");
  const check = practice.querySelector("#checkLiveAnswer");
  const send = async () => {
    const answer = normalizeLetter(input.value);
    if (!answer) return;
    check.disabled = true;
    const elapsed = Math.round((Date.now() - state.startedAt) / 1000);
    const item = await api("/api/practice/check", {
      method: "POST",
      body: JSON.stringify({
        session_id: state.currentSession.session_id,
        question_id: question.question_id,
        answer,
        time_spent_sec: elapsed,
      }),
    });
    state.liveResults.push(item);
    if (item.is_correct) {
      state.currentQuestionIndex += 1;
      renderLiveQuestion();
      return;
    }
    renderLiveQuestion(`
      <div class="result-item bad">
        <b>Неверно. Правильно: ${item.correct_answer}</b>
        <p>${item.correct_spelling || ""}</p>
        <p class="muted">${item.explanation || ""}</p>
        <button class="secondary-button" id="nextAfterRule" type="button">Дальше</button>
      </div>
    `);
    document.querySelector("#liveAnswer").disabled = true;
    document.querySelector("#checkLiveAnswer").disabled = true;
    const next = document.querySelector("#nextAfterRule");
    next.disabled = true;
    setTimeout(() => { next.disabled = false; }, 2500);
    next.addEventListener("click", () => {
      state.currentQuestionIndex += 1;
      renderLiveQuestion();
    });
  };
  check.addEventListener("click", send);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") send();
  });
  practice.querySelector("#cancelPractice").addEventListener("click", renderMode);
  input.focus();
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
  practice.querySelectorAll("[data-manual-answer]").forEach((input) => {
    input.addEventListener("input", () => {
      state.answers[input.dataset.manualAnswer] = input.dataset.kind === "line"
        ? input.value
        : normalizeLetter(input.value);
    });
  });
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
    if (state.manualInput) {
      return `
        <article class="question">
          <div class="question-head"><span>Вопрос ${index + 1}</span><span>${question.rule_name}</span></div>
          <div>${question.prompt}</div>
          ${question.rows.map((row, rowIndex) => `
            <div class="line-row static-line-row">
              <b>${rowIndex + 1}</b>
              <span class="line-words">${row.map((word) => `<span>${word}</span>`).join("")}</span>
            </div>
          `).join("")}
          <label class="answer-input-label">
            Ответ вручную
            <input data-manual-answer="${question.question_id}" data-kind="line" placeholder="например, 135" value="${state.answers[question.question_id] || ""}" />
          </label>
        </article>
      `;
    }
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
  if (state.manualInput) {
    return `
      <article class="question">
        <div class="question-head"><span>Вопрос ${index + 1}</span><span>${question.rule_name}</span></div>
        <div class="word-prompt">${question.prompt}</div>
        <label class="answer-input-label">
          Введите букву
          <input data-manual-answer="${question.question_id}" maxlength="1" autocomplete="off" value="${state.answers[question.question_id] || ""}" />
        </label>
      </article>
    `;
  }
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
  const answered = state.currentSession.questions.every((question) => String(state.answers[question.question_id] || "").trim());
  if (Object.keys(state.answers).length < total || !answered) {
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
          ${item.is_correct ? "" : `<p class="muted">${item.explanation || ""}</p>`}
        </div>
      `).join("")}
    </div>
    <div class="practice-actions">
      <button class="primary-button" type="button" id="againButton">Новая тренировка</button>
    </div>
  `;
  result.querySelector("#againButton").addEventListener("click", renderMode);
}

function renderTeacherStudentCards(students) {
  if (!students.length) {
    return `<p class="muted">Пока нет учеников, зарегистрированных по вашему коду.</p>`;
  }
  return students.map((student) => {
    const topErrors = student.top_errors.length
      ? student.top_errors.map((item) => `<li>${item.rule_name}: ${item.errors}</li>`).join("")
      : "<li>ошибок пока нет</li>";
    const pending = student.not_worked_out.length
      ? student.not_worked_out.slice(0, 5).map((item) => `<li>${item.correct_spelling || item.word}</li>`).join("")
      : "<li>очередь повторения пуста</li>";
    const errorBank = student.error_bank.length
      ? student.error_bank.slice(0, 6).map((item) => `<li>${item.correct_spelling || item.word}</li>`).join("")
      : "<li>копилка пуста</li>";
    return `
      <article class="student-card">
        <div class="student-card-head">
          <div>
            <b>${student.display_name}</b>
            <span class="muted">@${student.username}</span>
          </div>
          <div class="mini-stat"><b>${pct(student.correct, student.total)}</b><span>точность</span></div>
        </div>
        <div class="teacher-metrics">
          <div class="stat"><b>${student.total}</b><span>заданий решено</span></div>
          <div class="stat"><b>${student.untouched}</b><span>слов не затронуто</span></div>
          <div class="stat"><b>${student.error_bank.length}</b><span>в копилке ошибок</span></div>
        </div>
        <div class="student-lists">
          <div><h4>Больше ошибок</h4><ul>${topErrors}</ul></div>
          <div><h4>Не отработано</h4><ul>${pending}</ul></div>
          <div><h4>Копилка</h4><ul>${errorBank}</ul></div>
        </div>
      </article>
    `;
  }).join("");
}

async function renderTeacherDashboardPreview() {
  const main = document.querySelector(".main-panel");
  const old = document.querySelector("#teacherQuickPanel");
  old?.remove();
  const panel = document.createElement("section");
  panel.className = "teacher-quick-panel";
  panel.id = "teacherQuickPanel";
  panel.innerHTML = `<p class="muted">Загружаю быструю статистику...</p>`;
  main.insertBefore(panel, document.querySelector("#setupView"));
  try {
    const data = await api("/api/progress");
    panel.innerHTML = `
      <div class="section-head">
        <div>
          <p class="eyebrow">быстрая статистика</p>
          <h3>Ученики и зоны отработки</h3>
        </div>
        <div class="button-row">
          <button class="secondary-button" id="makeTestButton" type="button">Составить тест</button>
          <button class="secondary-button" id="downloadStudents" type="button">Скачать статистику</button>
          <button class="secondary-button" id="openFullProgress" type="button">Полная активность</button>
        </div>
      </div>
      <div class="student-card-grid">${renderTeacherStudentCards(data.teacher_dashboard.students)}</div>
    `;
    panel.querySelector("#openFullProgress").addEventListener("click", showProgress);
    panel.querySelector("#makeTestButton").addEventListener("click", showTestComposer);
    panel.querySelector("#downloadStudents").addEventListener("click", () => downloadRequest("/api/progress/export?section=students", "ege_students.csv"));
  } catch (err) {
    panel.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

function downloadButton(section, label) {
  return `<button class="ghost-button download-stat" data-section="${section}" type="button">${label}</button>`;
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
    <tr><td>${row.category}</td><td>${row.rule_name}</td><td>${row.total}</td><td>${pct(row.correct, row.total)}</td></tr>
  `).join("");
  const categoryRows = data.by_category.map((row) => `
    <tr><td>${row.category}</td><td>${row.total}</td><td>${pct(row.correct, row.total)}</td></tr>
  `).join("");
  const answerListRows = (rows) => rows.slice(0, 30).map((row) => `
    <tr>
      <td>${row.display_name}</td>
      <td>${row.category || ""}</td>
      <td>${row.rule_name || ""}</td>
      <td>${row.prompt}</td>
      <td>${row.given_answer || "—"} / ${row.correct_answer}</td>
    </tr>
  `).join("");
  const recentRows = data.recent.map((row) => `
    <tr>
      <td>${new Date(row.created_at).toLocaleString()}</td>
      <td>${row.display_name}</td>
      <td>${row.category || ""}</td>
      <td>${row.rule_name || ""}</td>
      <td>${row.prompt}</td>
      <td>${row.given_answer} / ${row.correct_answer}</td>
      <td>${row.is_correct ? "да" : "нет"}</td>
    </tr>
  `).join("");
  const teacherOverview = state.user.role === "teacher" && data.teacher_dashboard
    ? `<h3>Быстрая статистика учеников</h3><div class="student-card-grid">${renderTeacherStudentCards(data.teacher_dashboard.students)}</div>`
    : "";
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
      ${state.user.role !== "teacher" ? `
        <div class="progress-grid">
          <div class="stat"><b>${data.due_reviews}</b><span>слов в очереди повторения</span></div>
          <div class="stat"><b>${data.error_bank_count}</b><span>слов в копилке ошибок</span></div>
        </div>
      ` : ""}
      ${teacherOverview}
      ${state.user.role === "teacher" ? `<div class="table-head"><h3>Ученики</h3>${downloadButton("students", "Скачать статистику")}</div><table class="table"><tr><th>Имя</th><th>Ответов</th><th>Точность</th></tr>${studentRows}</table>` : ""}
      <div class="table-head"><h3>Группы</h3>${downloadButton("categories", "Скачать статистику")}</div>
      <table class="table"><tr><th>Группа</th><th>Ответов</th><th>Точность</th></tr>${categoryRows}</table>
      <div class="table-head"><h3>Подгруппы</h3>${downloadButton("rules", "Скачать статистику")}</div>
      <table class="table"><tr><th>Группа</th><th>Подгруппа</th><th>Ответов</th><th>Точность</th></tr>${ruleRows}</table>
      <details class="activity-details">
        <summary>Развернуть полную активность</summary>
        <div class="table-head"><h3>Решено верно</h3>${downloadButton("correct", "Скачать статистику")}</div>
        <table class="table"><tr><th>Ученик</th><th>Группа</th><th>Подгруппа</th><th>Задание</th><th>Ответ</th></tr>${answerListRows(data.correct_attempts)}</table>
        <div class="table-head"><h3>Решено неверно</h3>${downloadButton("incorrect", "Скачать статистику")}</div>
        <table class="table"><tr><th>Ученик</th><th>Группа</th><th>Подгруппа</th><th>Задание</th><th>Ответ</th></tr>${answerListRows(data.incorrect_attempts)}</table>
        <div class="table-head"><h3>Последние попытки</h3>${downloadButton("recent", "Скачать статистику")}</div>
        <table class="table"><tr><th>Дата</th><th>Пользователь</th><th>Группа</th><th>Подгруппа</th><th>Задание</th><th>Ответ</th><th>Верно</th></tr>${recentRows}</table>
      </details>
    </section>
  `;
  document.body.append(backdrop);
  backdrop.querySelector("#closeProgress").addEventListener("click", () => backdrop.remove());
  backdrop.querySelectorAll(".download-stat").forEach((button) => {
    button.addEventListener("click", () => downloadRequest(`/api/progress/export?section=${button.dataset.section}`, `ege_${button.dataset.section}.csv`));
  });
}

function showTestComposer() {
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  const ruleSelector = renderRuleSelector();
  backdrop.innerHTML = `
    <section class="progress-modal">
      <div class="panel-head">
        <div><p class="eyebrow">тест</p><h2>Составить тест</h2></div>
        <button class="secondary-button" id="closeTestComposer" type="button">Закрыть</button>
      </div>
      <div class="setup-grid">
        <label>
          Режим
          <select id="testMode">
            <option value="rule">Выбранные темы</option>
            <option value="mix">Микс</option>
            <option value="errors">Копилка ошибок</option>
            <option value="line">Строки</option>
          </select>
        </label>
        <label>
          Количество заданий
          <input id="testCount" type="number" min="1" max="60" value="${state.questionCount}" />
        </label>
      </div>
      <label class="manual-toggle">
        <input id="testIncludeErrors" type="checkbox" />
        <span>Добавить слова из копилки ошибок класса</span>
      </label>
      <div id="testRuleSelector">${ruleSelector}</div>
      <p class="error" id="testComposerError"></p>
      <div class="practice-actions">
        <button class="primary-button" id="downloadTest" type="button">Скачать .txt</button>
      </div>
    </section>
  `;
  document.body.append(backdrop);
  const refreshRules = () => {
    backdrop.querySelector("#testRuleSelector").classList.toggle("hidden", backdrop.querySelector("#testMode").value !== "rule");
  };
  backdrop.querySelector("#closeTestComposer").addEventListener("click", () => backdrop.remove());
  backdrop.querySelector("#testMode").addEventListener("change", refreshRules);
  backdrop.querySelectorAll("[data-category]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedCategory = button.dataset.category;
      state.selectedRuleIds = [];
      ensureRuleSelection();
      backdrop.remove();
      showTestComposer();
    });
  });
  backdrop.querySelector("#allRules")?.addEventListener("change", (event) => {
    state.selectedRuleIds = event.target.checked ? selectedRules().map((rule) => rule.rule_id) : [];
    backdrop.remove();
    showTestComposer();
  });
  backdrop.querySelectorAll("[data-rule-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const next = selectedRuleSet();
      checkbox.checked ? next.add(checkbox.dataset.ruleId) : next.delete(checkbox.dataset.ruleId);
      state.selectedRuleIds = [...next];
    });
  });
  backdrop.querySelector("#downloadTest").addEventListener("click", async () => {
    const error = backdrop.querySelector("#testComposerError");
    error.textContent = "";
    try {
      await downloadRequest("/api/teacher/test", "ege_test.txt", {
        method: "POST",
        body: JSON.stringify({
          mode: backdrop.querySelector("#testMode").value,
          count: backdrop.querySelector("#testCount").value,
          include_errors: backdrop.querySelector("#testIncludeErrors").checked,
          rule_ids: state.selectedRuleIds,
        }),
      });
    } catch (err) {
      error.textContent = err.message;
    }
  });
  refreshRules();
}

function renderAdminContent(data, closeButton = "") {
  const platform = data.platform;
  const teacherCards = data.teachers.map((teacher) => {
    const students = teacher.students_list.length
      ? teacher.students_list.map((student) => `
        <tr>
          <td>${student.display_name}</td>
          <td>${student.username}</td>
          <td>${student.attempts}</td>
          <td>${pct(student.correct, student.attempts)}</td>
          <td>
            <button class="ghost-button reset-password" data-user-id="${student.user_id}" data-username="${student.username}" type="button">
              ${student.password_reset_required ? "Ожидает новый пароль" : "Сбросить пароль"}
            </button>
          </td>
        </tr>
      `).join("")
      : `<tr><td colspan="5">Учеников пока нет</td></tr>`;
    return `
      <article class="admin-card">
        <div class="student-card-head">
          <div>
            <b>${teacher.display_name}</b>
            <span class="muted">@${teacher.username} · код ${teacher.teacher_code || "не задан"}</span>
          </div>
          <div class="button-row">
            <div class="mini-stat"><b>${teacher.students}</b><span>учеников</span></div>
            <button class="ghost-button reset-password" data-user-id="${teacher.user_id}" data-username="${teacher.username}" type="button">
              ${teacher.password_reset_required ? "Ожидает новый пароль" : "Сбросить пароль"}
            </button>
          </div>
        </div>
        <div class="teacher-metrics">
          <div class="stat"><b>${teacher.attempts}</b><span>ответов</span></div>
          <div class="stat"><b>${pct(teacher.correct, teacher.attempts)}</b><span>точность</span></div>
        </div>
        <table class="table"><tr><th>Ученик</th><th>Логин</th><th>Ответов</th><th>Точность</th><th>Пароль</th></tr>${students}</table>
      </article>
    `;
  }).join("");
  return `
    <div class="panel-head">
      <div><p class="eyebrow">админ</p><h2>Обзор платформы</h2></div>
      ${closeButton}
    </div>
    <div class="progress-grid">
      <div class="stat"><b>${platform.total}</b><span>ответов всего</span></div>
      <div class="stat"><b>${platform.active_users}</b><span>активных пользователей</span></div>
      <div class="stat"><b>${pct(platform.correct, platform.total)}</b><span>общая точность</span></div>
    </div>
    <div class="admin-list">${teacherCards}</div>
  `;
}

async function renderAdminDashboard() {
  view.innerHTML = `
    <section class="workspace admin-workspace">
      <section class="main-panel admin-page">
        <p class="muted">Загружаю админ-панель...</p>
      </section>
    </section>
  `;
  const panel = view.querySelector(".admin-page");
  try {
    const data = await api("/api/admin");
    panel.innerHTML = renderAdminContent(data);
    bindAdminActions(panel);
  } catch (err) {
    panel.innerHTML = `<p class="error">${err.message}</p>`;
  }
}

async function showAdmin() {
  const data = await api("/api/admin");
  const backdrop = document.createElement("div");
  backdrop.className = "modal-backdrop";
  backdrop.innerHTML = `
    <section class="progress-modal admin-modal">
      ${renderAdminContent(data, `<button class="secondary-button" id="closeAdmin" type="button">Закрыть</button>`)}
    </section>
  `;
  document.body.append(backdrop);
  backdrop.querySelector("#closeAdmin").addEventListener("click", () => backdrop.remove());
  bindAdminActions(backdrop);
}

function bindAdminActions(root) {
  root.querySelectorAll(".reset-password").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!confirm(`Сбросить пароль пользователю ${button.dataset.username}?`)) return;
      button.disabled = true;
      try {
        await api("/api/admin/reset-password", {
          method: "POST",
          body: JSON.stringify({ user_id: button.dataset.userId }),
        });
        button.textContent = "Ожидает новый пароль";
      } catch (err) {
        alert(err.message);
        button.disabled = false;
      }
    });
  });
}

restoreSession();

