from __future__ import annotations

import json
import random
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
WORDS_PATH = ROOT / "ege9_final_grouped_by_orthogram_v3.json"


@st.cache_data
def load_words() -> tuple[dict[str, list[dict]], list[dict]]:
    data = json.loads(WORDS_PATH.read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = {}
    words: list[dict] = []
    for category, rules in data["categories"].items():
        for rule_name, items in rules.items():
            grouped.setdefault(category, []).append({"rule_name": rule_name, "count": len(items)})
            for item in items:
                if item.get("variant") and item.get("correct_letter"):
                    words.append({**item, "category": category, "rule_name": rule_name})
    return grouped, words


def main() -> None:
    st.set_page_config(page_title="ЕГЭ 9", page_icon="9", layout="wide")
    grouped, words = load_words()

    st.title("ЕГЭ: задание 9")
    st.caption("Streamlit-версия для деплоя. Основное локальное приложение лежит в app/server.py.")

    mode = st.segmented_control("Режим", ["Правило", "Микс"], default="Правило")
    if mode == "Правило":
        category = st.selectbox("Большая группа", list(grouped))
        rule_names = [rule["rule_name"] for rule in grouped[category]]
        rule_name = st.selectbox("Подвыбор", rule_names)
        pool = [word for word in words if word["category"] == category and word["rule_name"] == rule_name]
    else:
        pool = words

    if "streamlit_question" not in st.session_state or st.button("Новое слово"):
        st.session_state.streamlit_question = random.choice(pool)
        st.session_state.streamlit_checked = False

    question = st.session_state.streamlit_question
    st.subheader(question["variant"])
    answer = st.radio("Буква", ["а", "о", "е", "и", "ы", "я", "ю", "э", "ё"], horizontal=True)

    if st.button("Проверить"):
        st.session_state.streamlit_checked = True
        if answer == question["correct_letter"]:
            st.success("Верно")
        else:
            st.error(f"Правильно: {question['correct_letter']}")
        st.write(question.get("correct_spelling", ""))
        st.caption(question.get("explanation", ""))


if __name__ == "__main__":
    main()
