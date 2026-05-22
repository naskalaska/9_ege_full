from __future__ import annotations

import hashlib
import csv
import io
import json
import mimetypes
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
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "app" / "static"
DATA_DIR = ROOT / "data"
IMAGES_DIR = ROOT / "images"
WORDS_PATH = ROOT / "ege9_final_grouped_by_orthogram_v4.json"
DB_PATH = DATA_DIR / "ege_app.db"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8088
REPEAT_ON_ERROR = 3
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin2026"

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


def ensure_admin_role_supported(con: sqlite3.Connection) -> None:
    table = con.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
    ).fetchone()
    if not table or "'admin'" in (table[0] or ""):
        return

    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("ALTER TABLE users RENAME TO users_old")
    con.execute(
        """
        CREATE TABLE users (
            user_id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'teacher', 'student')),
            password_salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
                teacher_code TEXT,
                teacher_id TEXT,
                password_reset_required INTEGER NOT NULL DEFAULT 0
            )
        """
    )
    con.execute(
        """
        INSERT INTO users
            (user_id, username, display_name, role, password_salt, password_hash,
             created_at, teacher_code, teacher_id)
        SELECT user_id, username, display_name, role, password_salt, password_hash,
               created_at, teacher_code, teacher_id
        FROM users_old
        """
    )
    con.execute("DROP TABLE users_old")
    con.execute("PRAGMA foreign_keys = ON")


def ensure_app_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'teacher', 'student')),
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
        ensure_column(con, "users", "password_reset_required", "INTEGER NOT NULL DEFAULT 0")
        ensure_admin_role_supported(con)
        ensure_column(con, "attempts", "scope_id", "TEXT")
        ensure_column(con, "attempts", "word_id", "TEXT")
        ensure_service_admin(con)
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


def ensure_service_admin(con: sqlite3.Connection) -> None:
    salt = secrets.token_hex(8)
    hashed = password_hash(ADMIN_PASSWORD, salt)
    existing = con.execute("SELECT 1 FROM users WHERE username = ?", (ADMIN_USERNAME,)).fetchone()
    if existing:
        con.execute(
            """
            UPDATE users
            SET user_id = 'user_admin',
                display_name = 'Администратор',
                role = 'admin',
                password_salt = ?,
                password_hash = ?,
                teacher_code = NULL,
                teacher_id = NULL
            WHERE username = ?
            """,
            (salt, hashed, ADMIN_USERNAME),
        )
        return
    con.execute(
        """
        INSERT INTO users
            (user_id, username, display_name, role, password_salt, password_hash, created_at)
        VALUES ('user_admin', ?, 'Администратор', 'admin', ?, ?, ?)
        """,
        (ADMIN_USERNAME, salt, hashed, now_iso()),
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

        existing = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            if not int(existing["password_reset_required"] or 0):
                raise ValueError("Такой логин уже занят.")
            if existing["role"] != role:
                raise ValueError("Для восстановления выберите прежнюю роль.")
            if role == "student" and existing["teacher_id"] != teacher_id:
                raise ValueError("Код учителя не совпадает с текущим аккаунтом.")
            salt = secrets.token_hex(8)
            con.execute(
                """
                UPDATE users
                SET display_name = COALESCE(NULLIF(?, ''), display_name),
                    password_salt = ?,
                    password_hash = ?,
                    password_reset_required = 0
                WHERE user_id = ?
                """,
                (display_name, salt, password_hash(password, salt), existing["user_id"]),
            )
            row = con.execute("SELECT * FROM users WHERE user_id = ?", (existing["user_id"],)).fetchone()
            return public_user(row)

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
RULE_BY_ID = {rule["rule_id"]: rule for rule in RULES}


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


def require_admin(user: dict[str, Any]) -> None:
    if user["role"] != "admin":
        raise PermissionError("Админ-страница доступна только администратору.")


def parse_json_body(handler: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if not length:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def scope_id_for(mode: str, rule_id: str | None = None, rule_ids: list[str] | None = None) -> str:
    if mode in {"rule", "word_letter"}:
        if rule_ids:
            digest = hashlib.sha1("|".join(sorted(rule_ids)).encode("utf-8")).hexdigest()[:16]
            return f"{mode}:rules:{digest}"
        return f"rule:{rule_id}"
    if mode == "mix":
        return "mix:all"
    if mode == "errors":
        return "errors:bank"
    return mode


def normalize_letter(value: Any) -> str:
    return str(value or "").strip().lower().replace("ё", "ё")[:1]


def letter_choices(word: dict[str, Any]) -> list[str]:
    correct = normalize_letter(word["correct_letter"])
    marker = f"{word['category']} {word['rule_name']} {word['dependency']} {word.get('root', '')}".lower()
    common_letters = ["а", "о", "е", "и", "ы", "я", "ю", "э", "ё", "у"]

    if "после ц" in marker and correct in {"и", "ы"}:
        priority = ["и", "ы"]
    elif "шип" in marker and correct in {"о", "ё", "е"}:
        priority = ["о", "ё", "е"]
    elif correct in {"а", "о"}:
        priority = ["а", "о"]
    elif correct in {"и", "е"}:
        priority = ["и", "е"]
    elif correct in {"я", "а"}:
        priority = ["я", "а"]
    elif correct in {"ю", "у"}:
        priority = ["ю", "у"]
    else:
        priority = [correct]

    choices: list[str] = []
    for letter in priority:
        letter = normalize_letter(letter)
        if letter and letter not in choices:
            choices.append(letter)

    if len(choices) <= 1:
        for letter in common_letters:
            letter = normalize_letter(letter)
            if letter and letter not in choices:
                choices.append(letter)
            if len(choices) >= 4:
                break

    random.shuffle(choices)
    if correct not in choices:
        choices[0] = correct
        random.shuffle(choices)
    return choices


def make_word_question(word: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": secrets.token_hex(8),
        "kind": "word",
        "source_word_id": word["id"],
        "prompt": word["variant"],
        "choices": letter_choices(word),
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


def shuffle_line_rows(rows: list[dict[str, Any]]) -> None:
    random.SystemRandom().shuffle(rows)
    if rows and rows[0]["is_correct"] and random.random() < 0.5:
        incorrect_indexes = [index for index, row in enumerate(rows[1:], start=1) if not row["is_correct"]]
        if incorrect_indexes:
            swap_index = random.choice(incorrect_indexes)
            rows[0], rows[swap_index] = rows[swap_index], rows[0]


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
        random.SystemRandom().shuffle(words)
        rows.append({"is_correct": False, "letter": None, "words": words})

    shuffle_line_rows(rows)
    correct_indexes = [str(index + 1) for index, row in enumerate(rows) if row["is_correct"]]
    correct_rows = [row for row in rows if row["is_correct"]]

    return {
        "question_id": secrets.token_hex(8),
        "kind": "line",
        "prompt": "Выберите все строки, где во всех трех словах пропущена одна и та же буква.",
        "rows": [[word["variant"] for word in row["words"]] for row in rows],
        "row_word_ids": [[word["id"] for word in row["words"]] for row in rows],
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


def record_attempt(
    con: sqlite3.Connection,
    user_id: str,
    session: dict[str, Any],
    question_id: str,
    question: dict[str, Any],
    given: str,
    elapsed: Any = None,
) -> dict[str, Any]:
    correct = question["correct_answer"].strip().lower()
    is_correct = int(given == correct)
    word_id = question.get("source_word_id")
    if word_id:
        if session["scope_id"] != scope_id_for("errors"):
            update_word_progress(con, user_id, session["scope_id"], word_id, is_correct)
        update_error_bank_progress(con, user_id, word_id, is_correct)
    elif question.get("kind") == "line":
        record_line_word_progress(con, user_id, session, question_id, question, given, is_correct, elapsed)
    con.execute(
        """
        INSERT INTO attempts
            (attempt_id, user_id, mode, scope_id, word_id, rule_id, category, rule_name,
             question_id, prompt, given_answer, correct_answer, is_correct, created_at, time_spent_sec)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            secrets.token_hex(12),
            user_id,
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
    return {
        "question_id": question_id,
        "is_correct": bool(is_correct),
        "given_answer": given,
        "correct_answer": correct,
        "explanation": question.get("explanation"),
        "correct_spelling": question.get("correct_spelling"),
    }


def update_error_bank_progress(
    con: sqlite3.Connection,
    user_id: str,
    word_id: str,
    is_correct: int,
) -> None:
    update_word_progress(con, user_id, scope_id_for("errors"), word_id, is_correct)


def record_word_attempt(
    con: sqlite3.Connection,
    user_id: str,
    session: dict[str, Any],
    question_id: str,
    word: dict[str, Any],
    given: str,
    is_correct: int,
    elapsed: Any = None,
) -> None:
    con.execute(
        """
        INSERT INTO attempts
            (attempt_id, user_id, mode, scope_id, word_id, rule_id, category, rule_name,
             question_id, prompt, given_answer, correct_answer, is_correct, created_at, time_spent_sec)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            secrets.token_hex(12),
            user_id,
            session["mode"],
            scope_id_for("errors"),
            word["id"],
            word["rule_id"],
            word["category"],
            word["rule_name"],
            question_id,
            word["variant"],
            given,
            word["correct_letter"],
            is_correct,
            now_iso(),
            elapsed,
        ),
    )


def record_line_word_progress(
    con: sqlite3.Connection,
    user_id: str,
    session: dict[str, Any],
    question_id: str,
    question: dict[str, Any],
    given: str,
    is_correct: int,
    elapsed: Any = None,
) -> None:
    correct_rows = set(question["correct_answer"])
    given_rows = set(given)
    affected_rows = correct_rows.symmetric_difference(given_rows)
    row_word_ids = question.get("row_word_ids") or []
    if is_correct:
        affected_rows = {str(index + 1) for index in range(len(row_word_ids))}

    for row_number in sorted(affected_rows):
        row_index = int(row_number) - 1
        if row_index < 0 or row_index >= len(row_word_ids):
            continue
        for word_id in row_word_ids[row_index]:
            word = WORD_BY_ID.get(word_id)
            if not word:
                continue
            update_word_progress(con, user_id, scope_id_for("line"), word_id, is_correct)
            update_error_bank_progress(con, user_id, word_id, is_correct)
            record_word_attempt(
                con,
                user_id,
                session,
                f"{question_id}:row{row_number}:{word_id}",
                word,
                f"строка {row_number}",
                is_correct,
                elapsed,
            )


def error_bank_words(user_id: str) -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute(
            """
            SELECT word_id, due_reviews, error_count, last_seen_at
            FROM word_progress
            WHERE user_id = ? AND scope_id = ? AND due_reviews > 0
            ORDER BY due_reviews DESC, error_count DESC, last_seen_at DESC
            """,
            (user_id, scope_id_for("errors")),
        ).fetchall()
    return [WORD_BY_ID[row["word_id"]] for row in rows if row["word_id"] in WORD_BY_ID]


def teacher_dashboard(con: sqlite3.Connection, teacher_id: str) -> dict[str, Any]:
    students = con.execute(
        """
        SELECT user_id, display_name, username
        FROM users
        WHERE role = 'student' AND teacher_id = ?
        ORDER BY display_name
        """,
        (teacher_id,),
    ).fetchall()
    result_students = []
    for student in students:
        user_id = student["user_id"]
        summary = con.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct,
                   COUNT(DISTINCT word_id) AS touched
            FROM attempts
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        error_rules = con.execute(
            """
            SELECT COALESCE(category, mode) AS category,
                   COALESCE(rule_name, mode) AS rule_name,
                   COUNT(*) AS errors
            FROM attempts
            WHERE user_id = ? AND is_correct = 0
            GROUP BY COALESCE(category, mode), COALESCE(rule_name, mode)
            ORDER BY errors DESC, rule_name
            LIMIT 3
            """,
            (user_id,),
        ).fetchall()
        due_words = con.execute(
            """
            SELECT wp.word_id, wp.due_reviews, wp.error_count
            FROM word_progress wp
            WHERE wp.user_id = ? AND wp.due_reviews > 0
            ORDER BY wp.due_reviews DESC, wp.error_count DESC, wp.last_seen_at DESC
            LIMIT 12
            """,
            (user_id,),
        ).fetchall()
        error_bank = con.execute(
            """
            SELECT word_id, error_count AS errors
            FROM word_progress
            WHERE user_id = ? AND scope_id = ? AND due_reviews > 0
            ORDER BY due_reviews DESC, error_count DESC, last_seen_at DESC
            LIMIT 20
            """,
            (user_id, scope_id_for("errors")),
        ).fetchall()
        touched = int(summary["touched"] or 0)
        result_students.append(
            {
                "user_id": user_id,
                "display_name": student["display_name"],
                "username": student["username"],
                "total": int(summary["total"] or 0),
                "correct": int(summary["correct"] or 0),
                "untouched": max(len(WORDS) - touched, 0),
                "not_worked_out": [
                    {
                        "word": WORD_BY_ID[row["word_id"]]["variant"],
                        "correct_spelling": WORD_BY_ID[row["word_id"]]["correct_spelling"],
                        "rule_name": WORD_BY_ID[row["word_id"]]["rule_name"],
                        "due_reviews": int(row["due_reviews"]),
                    }
                    for row in due_words
                    if row["word_id"] in WORD_BY_ID
                ],
                "top_errors": [dict(row) for row in error_rules],
                "error_bank": [
                    {
                        "word": WORD_BY_ID[row["word_id"]]["variant"],
                        "correct_spelling": WORD_BY_ID[row["word_id"]]["correct_spelling"],
                        "rule_name": WORD_BY_ID[row["word_id"]]["rule_name"],
                        "errors": int(row["errors"]),
                    }
                    for row in error_bank
                    if row["word_id"] in WORD_BY_ID
                ],
            }
        )
    return {"students": result_students}


def admin_overview(user: dict[str, Any]) -> dict[str, Any]:
    require_admin(user)
    with db() as con:
        platform = con.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(is_correct), 0) AS correct,
                   COUNT(DISTINCT user_id) AS active_users
            FROM attempts
            """
        ).fetchone()
        teachers = con.execute(
            """
            SELECT t.user_id, t.display_name, t.username, t.teacher_code, t.password_reset_required,
                   COUNT(DISTINCT s.user_id) AS students,
                   COUNT(a.attempt_id) AS attempts,
                   COALESCE(SUM(a.is_correct), 0) AS correct
            FROM users t
            LEFT JOIN users s ON s.teacher_id = t.user_id AND s.role = 'student'
            LEFT JOIN attempts a ON a.user_id = s.user_id
            WHERE t.role = 'teacher'
            GROUP BY t.user_id
            ORDER BY t.display_name
            """
        ).fetchall()
        teacher_ids = [row["user_id"] for row in teachers]
        student_rows = []
        if teacher_ids:
            placeholders = ",".join("?" for _ in teacher_ids)
            student_rows = con.execute(
                f"""
                SELECT s.user_id, s.teacher_id, s.display_name, s.username, s.password_reset_required,
                       COUNT(a.attempt_id) AS attempts,
                       COALESCE(SUM(a.is_correct), 0) AS correct
                FROM users s
                LEFT JOIN attempts a ON a.user_id = s.user_id
                WHERE s.role = 'student' AND s.teacher_id IN ({placeholders})
                GROUP BY s.user_id
                ORDER BY s.display_name
                """,
                tuple(teacher_ids),
            ).fetchall()
        students_by_teacher: dict[str, list[dict[str, Any]]] = {}
        for row in student_rows:
            students_by_teacher.setdefault(row["teacher_id"], []).append(dict(row))
    return {
        "platform": dict(platform),
        "teachers": [
            {
                **dict(row),
                "students_list": students_by_teacher.get(row["user_id"], []),
            }
            for row in teachers
        ],
    }


def start_practice(user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    mode = payload.get("mode")
    count = max(1, min(int(payload.get("count") or 10), 30))
    if mode in {"rule", "word_letter"}:
        raw_rule_ids = payload.get("rule_ids")
        if isinstance(raw_rule_ids, list):
            rule_ids = [str(rule_id) for rule_id in raw_rule_ids if str(rule_id) in WORDS_BY_RULE]
        else:
            rule_id = str(payload.get("rule_id") or "")
            rule_ids = [rule_id] if rule_id in WORDS_BY_RULE else []
        rule_ids = list(dict.fromkeys(rule_ids))

        pool: list[dict[str, Any]] = []
        for rule_id in rule_ids:
            pool.extend(WORDS_BY_RULE.get(rule_id, []))
        if not pool:
            raise ValueError("Выберите хотя бы одну подгруппу с заданиями.")
        scope_id = scope_id_for(mode, rule_ids=rule_ids)
        questions = [make_word_question(word) for word in pick_words_for_scope(user["user_id"], scope_id, pool, min(count, len(pool)))]
    elif mode == "mix":
        scope_id = scope_id_for(mode)
        questions = [make_word_question(word) for word in pick_words_for_scope(user["user_id"], scope_id, WORDS, min(count, len(WORDS)))]
    elif mode == "line":
        scope_id = scope_id_for(mode)
        questions = [make_line_question() for _ in range(count)]
    elif mode == "errors":
        scope_id = scope_id_for(mode)
        pool = error_bank_words(user["user_id"])
        if not pool:
            raise ValueError("Копилка ошибок пока пуста.")
        questions = [make_word_question(word) for word in pick_words_for_scope(user["user_id"], scope_id, pool, min(count, len(pool)))]
    else:
        raise ValueError("Неизвестный режим тренировки.")

    session_id = secrets.token_hex(12)
    answer_key: dict[str, dict[str, Any]] = {}
    public_questions = []
    for question in questions:
        correct_answer = question.pop("_correct_answer", "")
        answer_key[question["question_id"]] = {**question, "correct_answer": str(correct_answer).lower()}
        question.pop("row_word_ids", None)
        public_questions.append(question)

    PRACTICE_SESSIONS[session_id] = {
        "user_id": user["user_id"],
        "mode": mode,
        "scope_id": scope_id,
        "started_at": now_iso(),
        "questions": answer_key,
        "answered": {},
    }
    return {"session_id": session_id, "questions": public_questions}


def check_practice_answer(user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    session_id = payload.get("session_id")
    question_id = str(payload.get("question_id") or "")
    session = PRACTICE_SESSIONS.get(session_id)
    if not session or session["user_id"] != user["user_id"]:
        raise ValueError("Сессия тренировки не найдена.")
    question = session["questions"].get(question_id)
    if not question:
        raise ValueError("Вопрос не найден.")
    if question_id in session["answered"]:
        return session["answered"][question_id]
    given = normalize_given_answer(question, payload.get("answer", ""))
    with db() as con:
        result = record_attempt(con, user["user_id"], session, question_id, question, given, payload.get("time_spent_sec"))
    session["answered"][question_id] = result
    if len(session["answered"]) >= len(session["questions"]):
        PRACTICE_SESSIONS.pop(session_id, None)
    return result


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
            if question_id in session.get("answered", {}):
                results.append(session["answered"][question_id])
                continue
            given = normalize_given_answer(question, answers.get(question_id, ""))
            results.append(record_attempt(con, user["user_id"], session, question_id, question, given, elapsed))
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
        else:
            where = "WHERE a.user_id IN (SELECT user_id FROM users WHERE role = 'student' AND teacher_id = ?)"
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
              AND (
                (? = 'teacher' AND u.teacher_id = ?)
                OR (? = 'student' AND u.user_id = ?)
              )
            GROUP BY u.user_id
            ORDER BY u.display_name
            """,
            (user["role"], user["user_id"], user["role"], user["user_id"]),
        ).fetchall()
        by_rule = con.execute(
            f"""
            SELECT COALESCE(a.category, a.mode) AS category,
                   COALESCE(a.rule_name, a.mode) AS rule_name,
                   COUNT(*) AS total,
                   COALESCE(SUM(a.is_correct), 0) AS correct
            FROM attempts a
            {where}
            GROUP BY COALESCE(a.category, a.mode), COALESCE(a.rule_name, a.mode)
            ORDER BY category, rule_name
            """,
            params,
        ).fetchall()
        by_category = con.execute(
            f"""
            SELECT COALESCE(a.category, a.mode) AS category, COUNT(*) AS total,
                   COALESCE(SUM(a.is_correct), 0) AS correct
            FROM attempts a
            {where}
            GROUP BY COALESCE(a.category, a.mode)
            ORDER BY category
            """,
            params,
        ).fetchall()
        recent = con.execute(
            f"""
            SELECT a.created_at, a.mode, a.category, a.rule_name, a.prompt, a.given_answer,
                   a.correct_answer, a.is_correct, u.display_name
            FROM attempts a
            JOIN users u ON u.user_id = a.user_id
            {where}
            ORDER BY a.created_at DESC
            LIMIT 20
            """,
            params,
        ).fetchall()
        answer_lists = con.execute(
            f"""
            SELECT a.created_at, a.category, a.rule_name, a.prompt, a.given_answer,
                   a.correct_answer, a.is_correct, u.display_name
            FROM attempts a
            JOIN users u ON u.user_id = a.user_id
            {where}
            ORDER BY a.created_at DESC
            LIMIT 80
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
        error_bank = con.execute(
            """
            SELECT COUNT(*) AS total
            FROM word_progress
            WHERE user_id = ? AND scope_id = ? AND due_reviews > 0
            """,
            (user["user_id"], scope_id_for("errors")),
        ).fetchone() if user["role"] != "teacher" else {"total": 0}
    return {
        "summary": dict(summary),
        "due_reviews": int(due["due"]),
        "error_bank_count": int(error_bank["total"]),
        "teacher_dashboard": teacher_dashboard(con, user["user_id"]) if user["role"] == "teacher" else None,
        "by_student": [dict(row) for row in by_student],
        "by_category": [dict(row) for row in by_category],
        "by_rule": [dict(row) for row in by_rule],
        "recent": [dict(row) for row in recent],
        "correct_attempts": [dict(row) for row in answer_lists if int(row["is_correct"]) == 1],
        "incorrect_attempts": [dict(row) for row in answer_lists if int(row["is_correct"]) == 0],
    }


def require_teacher(user: dict[str, Any]) -> None:
    if user["role"] != "teacher":
        raise PermissionError("Функция доступна только учителю.")


def csv_bytes(rows: list[dict[str, Any]], headers: list[tuple[str, str]]) -> bytes:
    output = io.StringIO()
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";")
    writer.writerow([title for _, title in headers])
    for row in rows:
        writer.writerow([row.get(key, "") for key, _ in headers])
    return output.getvalue().encode("utf-8")


def progress_export(user: dict[str, Any], section: str) -> tuple[bytes, str, str]:
    data = progress_for(user)
    if section == "students":
        rows = [
            {
                "name": row["display_name"],
                "username": row["username"],
                "total": row["total"],
                "correct": row["correct"],
                "accuracy": f"{round((row['correct'] / row['total']) * 100) if row['total'] else 0}%",
            }
            for row in data["by_student"]
        ]
        headers = [("name", "Имя"), ("username", "Логин"), ("total", "Ответов"), ("correct", "Верно"), ("accuracy", "Точность")]
    elif section == "categories":
        rows = data["by_category"]
        headers = [("category", "Группа"), ("total", "Ответов"), ("correct", "Верно")]
    elif section == "rules":
        rows = data["by_rule"]
        headers = [("category", "Группа"), ("rule_name", "Подгруппа"), ("total", "Ответов"), ("correct", "Верно")]
    elif section == "correct":
        rows = data["correct_attempts"]
        headers = [("display_name", "Ученик"), ("category", "Группа"), ("rule_name", "Подгруппа"), ("prompt", "Задание"), ("given_answer", "Ответ"), ("correct_answer", "Правильно")]
    elif section == "incorrect":
        rows = data["incorrect_attempts"]
        headers = [("display_name", "Ученик"), ("category", "Группа"), ("rule_name", "Подгруппа"), ("prompt", "Задание"), ("given_answer", "Ответ"), ("correct_answer", "Правильно")]
    else:
        rows = data["recent"]
        headers = [("created_at", "Дата"), ("display_name", "Пользователь"), ("category", "Группа"), ("rule_name", "Подгруппа"), ("prompt", "Задание"), ("given_answer", "Ответ"), ("correct_answer", "Правильно"), ("is_correct", "Верно")]
    filename = f"ege_statistics_{section}.csv"
    return csv_bytes(rows, headers), filename, "text/csv; charset=utf-8"


def teacher_error_pool(teacher_id: str) -> list[dict[str, Any]]:
    with db() as con:
        rows = con.execute(
            """
            SELECT wp.word_id
            FROM word_progress wp
            JOIN users u ON u.user_id = wp.user_id
            WHERE u.teacher_id = ? AND wp.scope_id = ? AND wp.due_reviews > 0
            GROUP BY wp.word_id
            ORDER BY MAX(wp.due_reviews) DESC, MAX(wp.error_count) DESC, MAX(wp.last_seen_at) DESC
            """,
            (teacher_id, scope_id_for("errors")),
        ).fetchall()
    return [WORD_BY_ID[row["word_id"]] for row in rows if row["word_id"] in WORD_BY_ID]


def test_words_for_payload(user: dict[str, Any], payload: dict[str, Any], count: int) -> tuple[str, list[dict[str, Any]]]:
    mode = str(payload.get("mode") or "rule")
    include_errors = bool(payload.get("include_errors"))
    pool: list[dict[str, Any]] = []
    if mode == "rule":
        rule_ids = [str(rule_id) for rule_id in payload.get("rule_ids", []) if str(rule_id) in WORDS_BY_RULE]
        for rule_id in dict.fromkeys(rule_ids):
            pool.extend(WORDS_BY_RULE[rule_id])
        title = "Тест по выбранным темам"
    elif mode == "mix":
        pool = list(WORDS)
        title = "Смешанный тест"
    elif mode == "errors":
        pool = teacher_error_pool(user["user_id"])
        title = "Тест по копилке ошибок"
    else:
        raise ValueError("Для файла выберите режим: темы, микс или копилка ошибок.")
    if include_errors and mode != "errors":
        seen = {word["id"] for word in pool}
        pool.extend(word for word in teacher_error_pool(user["user_id"]) if word["id"] not in seen)
    if not pool:
        raise ValueError("Нет слов для составления теста.")
    random.shuffle(pool)
    return title, pool[: min(count, len(pool))]


def build_test_file(user: dict[str, Any], payload: dict[str, Any]) -> tuple[bytes, str, str]:
    require_teacher(user)
    count = max(1, min(int(payload.get("count") or 10), 60))
    mode = str(payload.get("mode") or "rule")
    lines = [f"Тест: {now_iso()}", ""]
    answers = ["Ответы", ""]
    if mode == "line":
        for index in range(1, count + 1):
            question = make_line_question()
            lines.append(f"{index}. {question['prompt']}")
            for row_index, row in enumerate(question["rows"], start=1):
                lines.append(f"   {row_index}) {', '.join(row)}")
            lines.append("")
            answers.append(f"{index}. {question['_correct_answer']} — {question['correct_spelling']}")
        filename = "ege_test_lines.txt"
    else:
        title, words = test_words_for_payload(user, payload, count)
        lines[0] = f"{title}: {now_iso()}"
        for index, word in enumerate(words, start=1):
            lines.append(f"{index}. {word['variant']}")
            answers.append(f"{index}. {word['correct_letter']} — {word['correct_spelling']} ({word['rule_name']})")
        filename = "ege_test.txt"
    body = "\n".join(lines + ["", ""] + answers) + "\n"
    return body.encode("utf-8"), filename, "text/plain; charset=utf-8"


def reset_user_password(admin: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    require_admin(admin)
    user_id = str(payload.get("user_id") or "")
    if not user_id or user_id == admin["user_id"]:
        raise ValueError("Нельзя сбросить пароль этому пользователю.")
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("Пользователь не найден.")
        salt = secrets.token_hex(8)
        con.execute(
            """
            UPDATE users
            SET password_salt = ?,
                password_hash = ?,
                password_reset_required = 1
            WHERE user_id = ?
            """,
            (salt, password_hash(secrets.token_urlsafe(32), salt), user_id),
        )
    return {"ok": True, "username": row["username"]}


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

    def send_download(self, body: bytes, filename: str, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
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
                    }
                )
            elif parsed.path == "/api/me":
                self.send_json({"user": self.current_user()})
            elif parsed.path == "/api/progress":
                self.send_json(progress_for(self.require_user()))
            elif parsed.path == "/api/progress/export":
                query = parse_qs(parsed.query)
                body, filename, content_type = progress_export(self.require_user(), query.get("section", ["recent"])[0])
                self.send_download(body, filename, content_type)
            elif parsed.path == "/api/admin":
                self.send_json(admin_overview(self.require_user()))
            elif parsed.path.startswith("/images/"):
                image_name = Path(parsed.path).name
                image_path = (IMAGES_DIR / image_name).resolve()
                if not str(image_path).startswith(str(IMAGES_DIR.resolve())) or not image_path.exists():
                    self.send_json({"error": "Image not found"}, HTTPStatus.NOT_FOUND)
                    return
                body = image_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mimetypes.guess_type(image_path.name)[0] or "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
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
                if row and int(row["password_reset_required"] or 0):
                    self.send_json({"error": "Пароль сброшен администратором. Зарегистрируйтесь с тем же логином и кодом учителя, чтобы задать новый пароль."}, HTTPStatus.UNAUTHORIZED)
                    return
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
            elif parsed.path == "/api/practice/check":
                self.send_json(check_practice_answer(self.require_user(), payload))
            elif parsed.path == "/api/teacher/test":
                body, filename, content_type = build_test_file(self.require_user(), payload)
                self.send_download(body, filename, content_type)
            elif parsed.path == "/api/admin/reset-password":
                self.send_json(reset_user_password(self.require_user(), payload))
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

