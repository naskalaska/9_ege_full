from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
DATA_DIR = ROOT / "data"
WORDS_PATH = ROOT / "ege9_final_grouped_by_orthogram_v3.json"
DB_PATH = DATA_DIR / "ege_app.db"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8088
REPEAT_ON_ERROR = 3

SESSIONS: dict[str, dict[str, Any]] = {}
PRACTICE_SESSIONS: dict[str, dict[str, Any]] = {}


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def password_hash(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def ensure_column(con: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_app_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('teacher', 'student')),
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                teacher_code TEXT,
                teacher_id TEXT
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                scope_id TEXT,
                word_id TEXT,
                rule_id TEXT,
                category TEXT,
                rule_name TEXT,
                question_id TEXT NOT NULL,
                prompt TEXT NOT NULL,
                given_answer TEXT,
                correct_answer TEXT NOT NULL,
                is_correct INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                time_spent_sec INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS word_progress (
                user_id TEXT NOT NULL,
                scope_id TEXT NOT NULL,
                word_id TEXT NOT NULL,
                cycle_seen INTEGER NOT NULL DEFAULT 0,
                due_reviews INTEGER NOT NULL DEFAULT 0,
                correct_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                last_seen_at TEXT,
                PRIMARY KEY(user_id, scope_id, word_id),
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_attempts_user_id ON attempts(user_id);
            CREATE INDEX IF NOT EXISTS idx_attempts_mode ON attempts(mode);
            CREATE INDEX IF NOT EXISTS idx_attempts_created_at ON attempts(created_at);
            CREATE INDEX IF NOT EXISTS idx_word_progress_user_scope ON word_progress(user_id, scope_id);
            """
        )
        ensure_column(con, "users", "teacher_code", "TEXT")
        ensure_column(con, "users", "teacher_id", "TEXT")
        ensure_column(con, "attempts", "scope_id", "TEXT")
        ensure_column(con, "attempts", "word_id", "TEXT")
        seed_user(con, "teacher", "teacher123", "teacher", "Учитель")
        seed_user(con, "student", "student123", "student", "Ученик")
        con.execute(
            """
            UPDATE users
            SET teacher_code = COALESCE(teacher_code, 'TEACHER-2026'),
                display_name = 'Учитель'
            WHERE username = 'teacher'
            """
        )
        con.execute(
            """
            UPDATE users
            SET teacher_id = COALESCE(teacher_id, 'user_teacher'),
                display_name = 'Ученик'
            WHERE username = 'student'
            """
        )


def seed_user(con: sqlite3.Connection, username: str, password: str, role: str, display_name: str) -> None:
    exists = con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if exists:
        return
    salt = secrets.token_hex(8)
    con.execute(
        """
        INSERT INTO users (user_id, username, display_name, role, password_salt, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (f"user_{username}", username, display_name, role, salt, password_hash(password, salt), now_iso()),
    )


def make_teacher_code() -> str:
    return f"T-{secrets.token_hex(3).upper()}"


def register_user(payload: dict[str, Any]) -> dict[str, Any]:
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    display_name = str(payload.get("display_name") or username).strip()
    role = str(payload.get("role") or "student").strip()
    teacher_code = str(payload.get("teacher_code") or "").strip().upper()

    if role not in {"teacher", "student"}:
        raise ValueError("Неизвестная роль.")
    if len(username) < 3:
        raise ValueError("Логин должен быть не короче 3 символов.")
    if len(password) < 6:
        raise ValueError("Пароль должен быть не короче 6 символов.")
    if role == "student" and not teacher_code:
        raise ValueError("Для регистрации ученика нужен код учителя.")

    with db() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ValueError("Такой логин уже занят.")

        teacher_id = None
        own_teacher_code = None
        if role == "student":
            teacher = con.execute(
                "SELECT user_id FROM users WHERE role = 'teacher' AND UPPER(teacher_code) = ?",
                (teacher_code,),
            ).fetchone()
            if not teacher:
                raise ValueError("Код учителя не найден.")
            teacher_id = teacher["user_id"]
        else:
            own_teacher_code = make_teacher_code()

        salt = secrets.token_hex(8)
        user_id = f"user_{secrets.token_hex(8)}"
        con.execute(
            """
            INSERT INTO users
                (user_id, username, display_name, role, password_salt, password_hash,
                 created_at, teacher_code, teacher_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                display_name,
                role,
                salt,
                password_hash(password, salt),
                now_iso(),
                own_teacher_code,
                teacher_id,
            ),
        )
        row = con.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return public_user(row)


def load_words() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = json.loads(WORDS_PATH.read_text(encoding="utf-8"))
    words: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for category, grouped_rules in data.get("categories", {}).items():
        for rule_name, items in grouped_rules.items():
            rule_id = make_rule_id(category, rule_name)
            rule_words = []
            for item in items:
                if not item.get("variant") or not item.get("correct_letter"):
                    continue
                word = {
                    "id": str(item.get("id") or f"{rule_id}_{len(words)}"),
                    "category": category,
                    "rule_name": rule_name,
                    "rule_id": rule_id,
                    "variant": item.get("variant"),
                    "correct_letter": str(item.get("correct_letter") or item.get("answer")).strip().lower(),
                    "correct_spelling": item.get("correct_spelling") or "",
                    "explanation": item.get("explanation") or "",
                    "dependency": item.get("dependency") or "",
                    "address": item.get("first_address") or "",
                }
                words.append(word)
                rule_words.append(word)
            rules.append(
                {
                    "rule_id": rule_id,
                    "category": category,
                    "rule_name": rule_name,
                    "count": len(rule_words),
                }
            )
    return words, rules


def make_rule_id(category: str, rule_name: str) -> str:
    return hashlib.sha1(f"{category}::{rule_name}".encode("utf-8")).hexdigest()[:16]


WORDS, RULES = load_words()
WORD_BY_ID = {word["id"]: word for word in WORDS}
WORDS_BY_RULE: dict[str, list[dict[str, Any]]] = {}
for word in WORDS:
    WORDS_BY_RULE.setdefault(word["rule_id"], []).append(word)


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "teacher_code": row["teacher_code"] if row["role"] == "teacher" else None,
        "teacher_id": row["teacher_id"] if row["role"] == "student" else None,
    }


def parse_json_body(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if not length:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def scope_id_for(mode: str, rule_id: str | None = None) -> str:
    if mode == "rule":
        return f"rule:{rule_id}"
    if mode == "mix":
        return "mix:all"
    return mode


def letter_choices(correct: str) -> list[str]:
    base = ["а", "о", "е", "и", "ы", "я", "ю", "э"]
    choices = [correct]
    pool = [letter for letter in base if letter != correct]
    random.shuffle(pool)
    choices.extend(pool[:3])
    random.shuffle(choices)
    return choices


def make_word_question(word: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": secrets.token_hex(8),
        "kind": "word",
        "source_word_id": word["id"],
        "prompt": word["variant"],
        "choices": letter_choices(word["correct_letter"]),
        "category": word["category"],
        "rule_id": word["rule_id"],
        "rule_name": word["rule_name"],
        "explanation": word["explanation"],
        "correct_spelling": word["correct_spelling"],
        "address": word["address"],
        "_correct_answer": word["correct_letter"],
    }


def pick_words_for_scope(user_id: str, scope_id: str, pool: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    if not pool:
        return []

    pool_by_id = {word["id"]: word for word in pool}
    selected: list[dict[str, Any]] = []
    with db() as con:
        rows = con.execute(
            """
            SELECT word_id, cycle_seen, due_reviews, last_seen_at
            FROM word_progress
            WHERE user_id = ? AND scope_id = ?
            """,
            (user_id, scope_id),
        ).fetchall()
        progress = {row["word_id"]: row for row in rows if row["word_id"] in pool_by_id}

        due = [
            row
            for row in sorted(progress.values(), key=lambda item: (item["last_seen_at"] or "", item["word_id"]))
            if int(row["due_reviews"]) > 0
        ]
        for row in due[:count]:
            selected.append(pool_by_id[row["word_id"]])

        remaining = count - len(selected)
        if remaining <= 0:
            return selected

        selected_ids = {word["id"] for word in selected}
        fresh = [
            word
            for word in pool
            if word["id"] not in selected_ids
            and (word["id"] not in progress or int(progress[word["id"]]["cycle_seen"]) == 0)
        ]
        if not fresh:
            con.execute(
                """
                UPDATE word_progress
                SET cycle_seen = 0
                WHERE user_id = ? AND scope_id = ? AND due_reviews = 0
                """,
                (user_id, scope_id),
            )
            fresh = [word for word in pool if word["id"] not in selected_ids]

    random.shuffle(fresh)
    selected.extend(fresh[:remaining])
    return selected


def update_word_progress(
    con: sqlite3.Connection,
    user_id: str,
    scope_id: str,
    word_id: str,
    is_correct: int,
) -> None:
    current = con.execute(
        """
        SELECT due_reviews, correct_count, error_count
        FROM word_progress
        WHERE user_id = ? AND scope_id = ? AND word_id = ?
        """,
        (user_id, scope_id, word_id),
    ).fetchone()
    due_reviews = int(current["due_reviews"]) if current else 0
    correct_count = int(current["correct_count"]) if current else 0
    error_count = int(current["error_count"]) if current else 0

    if is_correct:
        due_reviews = max(due_reviews - 1, 0)
        correct_count += 1
        cycle_seen = 1
    else:
        due_reviews = REPEAT_ON_ERROR
        error_count += 1
        cycle_seen = 0

    con.execute(
        """
        INSERT INTO word_progress
            (user_id, scope_id, word_id, cycle_seen, due_reviews, correct_count, error_count, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, scope_id, word_id) DO UPDATE SET
            cycle_seen = excluded.cycle_seen,
            due_reviews = excluded.due_reviews,
            correct_count = excluded.correct_count,
            error_count = excluded.error_count,
            last_seen_at = excluded.last_seen_at
        """,
        (user_id, scope_id, word_id, cycle_seen, due_reviews, correct_count, error_count, now_iso()),
    )


LINE_PAIRS = {
    "а/о": {"letters": ("а", "о"), "label": "А/О"},
    "и/е": {"letters": ("и", "е"), "label": "И/Е"},
    "и/ы после ц": {"letters": ("и", "ы"), "label": "И/Ы после Ц"},
    "о/ё после шипящих": {"letters": ("о", "ё"), "label": "О/Ё после шипящих"},
}


def line_pair_key(word: dict[str, Any]) -> str | None:
    letter = word["correct_letter"]
    marker = f"{word['category']} {word['rule_name']} {word['dependency']}".lower()
    if "ц" in marker and letter in {"и", "ы"}:
        return "и/ы после ц"
    if "шип" in marker and letter in {"о", "ё"}:
        return "о/ё после шипящих"
    if letter in {"а", "о"}:
        return "а/о"
    if letter in {"и", "е"}:
        return "и/е"
    return None


def line_pair_pools() -> dict[str, dict[str, list[dict[str, Any]]]]:
    pools: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for word in WORDS:
        key = line_pair_key(word)
        if not key:
            continue
        pools.setdefault(key, {}).setdefault(word["correct_letter"], []).append(word)
    return pools


def sample_words(pool: list[dict[str, Any]], count: int, used_ids: set[str]) -> list[dict[str, Any]]:
    fresh = [word for word in pool if word["id"] not in used_ids]
    source = fresh if len(fresh) >= count else pool
    picked = random.sample(source, count)
    used_ids.update(word["id"] for word in picked)
    return picked


def make_line_question() -> dict[str, Any]:
    pools = line_pair_pools()
    viable_pairs = [
        pair_key
        for pair_key, by_letter in pools.items()
        if all(len(by_letter.get(letter, [])) >= 3 for letter in LINE_PAIRS[pair_key]["letters"])
    ]
    pair_key = random.choice(viable_pairs)
    letters = LINE_PAIRS[pair_key]["letters"]
    by_letter = pools[pair_key]
    used_ids: set[str] = set()

    rows: list[dict[str, Any]] = []
    correct_row_count = random.choice([2, 3])
    for _ in range(correct_row_count):
        letter = random.choice(letters)
        rows.append(
            {
                "is_correct": True,
                "letter": letter,
                "words": sample_words(by_letter[letter], 3, used_ids),
            }
        )

    for _ in range(5 - correct_row_count):
        first, second = letters
        pattern = random.choice([(first, first, second), (first, second, second)])
        words: list[dict[str, Any]] = []
        for letter in pattern:
            words.extend(sample_words(by_letter[letter], 1, used_ids))
        random.shuffle(words)
        rows.append({"is_correct": False, "letter": None, "words": words})

    random.shuffle(rows)
    correct_indexes = [str(index + 1) for index, row in enumerate(rows) if row["is_correct"]]
    correct_rows = [row for row in rows if row["is_correct"]]

    return {
        "question_id": secrets.token_hex(8),
        "kind": "line",
        "prompt": "Выберите все строки, где во всех трех словах пропущена одна и та же буква.",
        "rows": [[word["variant"] for word in row["words"]] for row in rows],
        "choices": [str(number) for number in range(1, len(rows) + 1)],
        "category": "Строка",
        "rule_id": "line_same_letter",
        "rule_name": f"Строка: {LINE_PAIRS[pair_key]['label']}",
        "explanation": (
            f"В этом варианте работала пара {LINE_PAIRS[pair_key]['label']}. "
            f"Правильные строки: {', '.join(correct_indexes)}."
        ),
        "correct_spelling": "; ".join(
            word["correct_spelling"] for row in correct_rows for word in row["words"]
        ),
        "address": "",
        "_correct_answer": "".join(correct_indexes),
    }


def normalize_given_answer(question: dict[str, Any], value: Any) -> str:
    raw = str(value or "").strip().lower()
    if question.get("kind") == "line":
        return "".join(sorted(char for char in raw if char.isdigit()))
    return raw


def start_practice(user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode")
    count = max(1, min(int(payload.get("count") or 10), 30))
    if mode == "rule":
        rule_id = payload.get("rule_id")
        pool = WORDS_BY_RULE.get(rule_id, [])
        if not pool:
            raise ValueError("Правило не найдено или в нем нет слов.")
        scope_id = scope_id_for(mode, rule_id)
        questions = [make_word_question(word) for word in pick_words_for_scope(user["user_id"], scope_id, pool, min(count, len(pool)))]
    elif mode == "mix":
        scope_id = scope_id_for(mode)
        questions = [make_word_question(word) for word in pick_words_for_scope(user["user_id"], scope_id, WORDS, min(count, len(WORDS)))]
    elif mode == "line":
        scope_id = scope_id_for(mode)
        questions = [make_line_question() for _ in range(count)]
    else:
        raise ValueError("Неизвестный режим тренировки.")

    session_id = secrets.token_hex(12)
    answer_key: dict[str, dict[str, Any]] = {}
    public_questions = []
    for question in questions:
        correct_answer = question.pop("_correct_answer", "")
        answer_key[question["question_id"]] = {**question, "correct_answer": str(correct_answer).lower()}
        public_questions.append(question)

    PRACTICE_SESSIONS[session_id] = {
        "user_id": user["user_id"],
        "mode": mode,
        "scope_id": scope_id,
        "started_at": now_iso(),
        "questions": answer_key,
    }
    return {"session_id": session_id, "questions": public_questions}


def submit_practice(user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("session_id")
    answers = payload.get("answers") or {}
    elapsed = payload.get("time_spent_sec")
    session = PRACTICE_SESSIONS.get(session_id)
    if not session or session["user_id"] != user["user_id"]:
        raise ValueError("Сессия тренировки не найдена.")

    results = []
    with db() as con:
        for question_id, question in session["questions"].items():
            given = normalize_given_answer(question, answers.get(question_id, ""))
            correct = question["correct_answer"].strip().lower()
            is_correct = int(given == correct)
            word_id = question.get("source_word_id")
            if word_id:
                update_word_progress(con, user["user_id"], session["scope_id"], word_id, is_correct)
            con.execute(
                """
                INSERT INTO attempts
                    (attempt_id, user_id, mode, scope_id, word_id, rule_id, category, rule_name,
                     question_id, prompt, given_answer, correct_answer, is_correct, created_at, time_spent_sec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    secrets.token_hex(12),
                    user["user_id"],
                    session["mode"],
                    session["scope_id"],
                    word_id,
                    question.get("rule_id"),
                    question.get("category"),
                    question.get("rule_name"),
                    question_id,
                    question.get("prompt"),
                    given,
                    correct,
                    is_correct,
                    now_iso(),
                    elapsed,
                ),
            )
            results.append(
                {
                    "question_id": question_id,
                    "is_correct": bool(is_correct),
                    "given_answer": given,
                    "correct_answer": correct,
                    "explanation": question.get("explanation"),
                    "correct_spelling": question.get("correct_spelling"),
                }
            )
    PRACTICE_SESSIONS.pop(session_id, None)
    return {
        "results": results,
        "correct": sum(1 for result in results if result["is_correct"]),
        "total": len(results),
    }


def progress_for(user: dict[str, Any]) -> dict[str, Any]:
    with db() as con:
        params: tuple[Any, ...] = ()
        where = ""
        if user["role"] != "teacher":
            where = "WHERE a.user_id = ?"
            params = (user["user_id"],)
        summary = con.execute(
            f"""
            SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct
            FROM attempts a
            {where}
            """,
            params,
        ).fetchone()
        by_student = con.execute(
            """
            SELECT u.display_name, u.username, COUNT(a.attempt_id) AS total,
                   COALESCE(SUM(a.is_correct), 0) AS correct
            FROM users u
            LEFT JOIN attempts a ON a.user_id = u.user_id
            WHERE u.role = 'student'
            GROUP BY u.user_id
            ORDER BY u.display_name
            """
        ).fetchall()
        by_rule = con.execute(
            f"""
            SELECT COALESCE(a.rule_name, a.mode) AS rule_name, COUNT(*) AS total,
                   COALESCE(SUM(a.is_correct), 0) AS correct
            FROM attempts a
            {where}
            GROUP BY COALESCE(a.rule_name, a.mode)
            ORDER BY total DESC, rule_name
            LIMIT 12
            """,
            params,
        ).fetchall()
        recent = con.execute(
            f"""
            SELECT a.created_at, a.mode, a.rule_name, a.prompt, a.given_answer,
                   a.correct_answer, a.is_correct, u.display_name
            FROM attempts a
            JOIN users u ON u.user_id = a.user_id
            {where}
            ORDER BY a.created_at DESC
            LIMIT 20
            """,
            params,
        ).fetchall()
        due = con.execute(
            """
            SELECT COUNT(*) AS due
            FROM word_progress
            WHERE user_id = ? AND due_reviews > 0
            """,
            (user["user_id"],),
        ).fetchone() if user["role"] != "teacher" else {"due": 0}
    return {
        "summary": dict(summary),
        "due_reviews": int(due["due"]),
        "by_student": [dict(row) for row in by_student],
        "by_rule": [dict(row) for row in by_rule],
        "recent": [dict(row) for row in recent],
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def current_user(self) -> dict[str, Any] | None:
        header = self.headers.get("Authorization", "")
        token = header.removeprefix("Bearer ").strip()
        session = SESSIONS.get(token)
        return session["user"] if session else None

    def require_user(self) -> dict[str, Any]:
        user = self.current_user()
        if not user:
            raise PermissionError("Нужен вход в приложение.")
        return user

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/bootstrap":
                grouped: dict[str, list[dict[str, Any]]] = {}
                for rule in RULES:
                    grouped.setdefault(rule["category"], []).append(rule)
                self.send_json(
                    {
                        "rules": grouped,
                        "word_count": len(WORDS),
                        "repeat_on_error": REPEAT_ON_ERROR,
                        "demo_accounts": [
                            {"role": "teacher", "login": "teacher", "password": "teacher123"},
                            {"role": "student", "login": "student", "password": "student123"},
                        ],
                    }
                )
            elif parsed.path == "/api/me":
                self.send_json({"user": self.current_user()})
            elif parsed.path == "/api/progress":
                self.send_json(progress_for(self.require_user()))
            else:
                if parsed.path == "/":
                    self.path = "/index.html"
                super().do_GET()
        except PermissionError as error:
            self.send_json({"error": str(error)}, HTTPStatus.UNAUTHORIZED)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = parse_json_body(self)
            if parsed.path == "/api/login":
                username = str(payload.get("username") or "").strip()
                password = str(payload.get("password") or "")
                with db() as con:
                    row = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
                if not row or password_hash(password, row["password_salt"]) != row["password_hash"]:
                    self.send_json({"error": "Неверный логин или пароль."}, HTTPStatus.UNAUTHORIZED)
                    return
                token = secrets.token_urlsafe(24)
                user = public_user(row)
                SESSIONS[token] = {"user": user, "created_at": now_iso()}
                self.send_json({"token": token, "user": user})
            elif parsed.path == "/api/register":
                user = register_user(payload)
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = {"user": user, "created_at": now_iso()}
                self.send_json({"token": token, "user": user})
            elif parsed.path == "/api/logout":
                header = self.headers.get("Authorization", "")
                token = header.removeprefix("Bearer ").strip()
                SESSIONS.pop(token, None)
                self.send_json({"ok": True})
            elif parsed.path == "/api/practice/start":
                self.send_json(start_practice(self.require_user(), payload))
            elif parsed.path == "/api/practice/submit":
                self.send_json(submit_practice(self.require_user(), payload))
            else:
                self.send_json({"error": "Unknown endpoint"}, HTTPStatus.NOT_FOUND)
        except PermissionError as error:
            self.send_json({"error": str(error)}, HTTPStatus.UNAUTHORIZED)
        except Exception as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)


def main() -> int:
    configure_console()
    ensure_app_db()
    host = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("HOST", DEFAULT_HOST)
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"EGE Russian app: http://{host}:{port}")
    print(f"Words loaded: {len(WORDS)}; rules: {len(RULES)}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
