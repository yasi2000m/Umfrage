from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("SURVEY_DB_PATH", BASE_DIR / "data" / "surveys.db"))


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
    with connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS surveys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                question_type TEXT NOT NULL CHECK(question_type IN ('single', 'multiple')),
                free_text INTEGER NOT NULL DEFAULT 0,
                position INTEGER NOT NULL,
                FOREIGN KEY (survey_id) REFERENCES surveys(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                position INTEGER NOT NULL,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                survey_id INTEGER NOT NULL,
                submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (survey_id) REFERENCES surveys(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                option_id INTEGER NOT NULL,
                FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
                FOREIGN KEY (option_id) REFERENCES options(id) ON DELETE CASCADE,
                UNIQUE(submission_id, question_id, option_id)
            );
            CREATE TABLE IF NOT EXISTS text_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                submission_id INTEGER NOT NULL,
                question_id INTEGER NOT NULL,
                answer_text TEXT NOT NULL,
                FOREIGN KEY (submission_id) REFERENCES submissions(id) ON DELETE CASCADE,
                FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
                UNIQUE(submission_id, question_id)
            );
            """
        )
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(questions)")}
        if "free_text" not in columns:
            conn.execute(
                "ALTER TABLE questions ADD COLUMN free_text INTEGER NOT NULL DEFAULT 0"
            )


def _validate_survey_data(title: str, questions: list[dict]) -> tuple[str, list[dict]]:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Bitte einen Titel für die Umfrage eingeben.")
    if not questions:
        raise ValueError("Bitte mindestens eine Frage erstellen.")

    clean_questions = []
    for index, question in enumerate(questions):
        text = str(question.get("text", "")).strip()
        question_type = question.get("type", "single")
        if not text:
            raise ValueError(f"Frage {index + 1} hat keinen Fragetext.")
        if question_type not in {"single", "multiple", "text"}:
            raise ValueError("Ungültiger Fragetyp.")

        options = []
        if question_type != "text":
            options = [
                str(value).strip()
                for value in question.get("options", [])
                if str(value).strip()
            ]
            if len(options) < 2:
                raise ValueError(f"Frage {index + 1} benötigt mindestens zwei Optionen.")
            if len({value.casefold() for value in options}) != len(options):
                raise ValueError(f"Frage {index + 1} enthält doppelte Optionen.")
        clean_questions.append({"text": text, "type": question_type, "options": options})
    return clean_title, clean_questions


def _insert_questions(conn: sqlite3.Connection, survey_id: int, questions: list[dict]) -> None:
    for position, question in enumerate(questions):
        is_text = question["type"] == "text"
        stored_type = "single" if is_text else question["type"]
        cursor = conn.execute(
            """INSERT INTO questions
               (survey_id, question_text, question_type, free_text, position)
               VALUES (?, ?, ?, ?, ?)""",
            (survey_id, question["text"], stored_type, int(is_text), position),
        )
        question_id = int(cursor.lastrowid)
        if not is_text:
            conn.executemany(
                "INSERT INTO options (question_id, label, position) VALUES (?, ?, ?)",
                [
                    (question_id, label, option_position)
                    for option_position, label in enumerate(question["options"])
                ],
            )


def create_survey(title: str, questions: list[dict]) -> int:
    clean_title, clean_questions = _validate_survey_data(title, questions)
    with connection() as conn:
        cursor = conn.execute("INSERT INTO surveys (title) VALUES (?)", (clean_title,))
        survey_id = int(cursor.lastrowid)
        _insert_questions(conn, survey_id, clean_questions)
    return survey_id


def update_survey(
    survey_id: int,
    title: str,
    questions: list[dict],
    allow_response_reset: bool = False,
) -> None:
    clean_title, clean_questions = _validate_survey_data(title, questions)
    with connection() as conn:
        if not conn.execute("SELECT id FROM surveys WHERE id = ?", (survey_id,)).fetchone():
            raise ValueError("Umfrage nicht gefunden.")

        rows = conn.execute(
            """SELECT id, question_text, question_type, free_text FROM questions
               WHERE survey_id = ? ORDER BY position, id""",
            (survey_id,),
        ).fetchall()
        existing_questions = []
        for row in rows:
            options = conn.execute(
                "SELECT label FROM options WHERE question_id = ? ORDER BY position, id",
                (row["id"],),
            ).fetchall()
            existing_questions.append(
                {
                    "text": row["question_text"],
                    "type": "text" if row["free_text"] else row["question_type"],
                    "options": [option["label"] for option in options],
                }
            )

        conn.execute("UPDATE surveys SET title = ? WHERE id = ?", (clean_title, survey_id))
        if existing_questions == clean_questions:
            return

        count = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE survey_id = ?", (survey_id,)
        ).fetchone()[0]
        if count and not allow_response_reset:
            raise ValueError("Bitte das Zurücksetzen der vorhandenen Antworten bestätigen.")
        conn.execute("DELETE FROM submissions WHERE survey_id = ?", (survey_id,))
        conn.execute("DELETE FROM questions WHERE survey_id = ?", (survey_id,))
        _insert_questions(conn, survey_id, clean_questions)


def delete_survey(survey_id: int) -> None:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM surveys WHERE id = ?", (survey_id,))
        if cursor.rowcount == 0:
            raise ValueError("Umfrage nicht gefunden.")


def list_surveys(active_only: bool = False) -> list[dict]:
    condition = "WHERE active = 1" if active_only else ""
    with connection() as conn:
        survey_rows = conn.execute(
            f"SELECT id, title, created_at, active FROM surveys {condition} ORDER BY id DESC"
        ).fetchall()
        surveys = []
        for survey_row in survey_rows:
            survey = dict(survey_row)
            question_rows = conn.execute(
                """SELECT id, question_text, question_type, free_text, position
                   FROM questions WHERE survey_id = ? ORDER BY position, id""",
                (survey["id"],),
            ).fetchall()
            questions = []
            for question_row in question_rows:
                question = dict(question_row)
                question["question_type"] = (
                    "text" if question.pop("free_text") else question["question_type"]
                )
                option_rows = conn.execute(
                    "SELECT id, label, position FROM options WHERE question_id = ? ORDER BY position, id",
                    (question["id"],),
                ).fetchall()
                question["options"] = [dict(row) for row in option_rows]
                questions.append(question)
            survey["questions"] = questions
            surveys.append(survey)
        return surveys


def submit_survey(survey_id: int, answers: dict[int, list[int] | str]) -> int:
    with connection() as conn:
        if not conn.execute(
            "SELECT id FROM surveys WHERE id = ? AND active = 1", (survey_id,)
        ).fetchone():
            raise ValueError("Diese Umfrage ist nicht mehr aktiv.")

        questions = conn.execute(
            "SELECT id, question_type, free_text FROM questions WHERE survey_id = ?",
            (survey_id,),
        ).fetchall()
        choice_answers: dict[int, list[int]] = {}
        written_answers: dict[int, str] = {}
        for question in questions:
            question_id = int(question["id"])
            if question["free_text"]:
                written = str(answers.get(question_id, "")).strip()
                if not written:
                    raise ValueError("Bitte jede Frage beantworten.")
                written_answers[question_id] = written
                continue

            raw_values = answers.get(question_id, [])
            selected = list(dict.fromkeys(int(value) for value in raw_values))
            if not selected:
                raise ValueError("Bitte jede Frage beantworten.")
            if question["question_type"] == "single" and len(selected) != 1:
                raise ValueError("Bei einer Einfachauswahl ist nur eine Antwort erlaubt.")
            placeholders = ",".join("?" for _ in selected)
            valid_count = conn.execute(
                f"SELECT COUNT(*) FROM options WHERE question_id = ? AND id IN ({placeholders})",
                (question_id, *selected),
            ).fetchone()[0]
            if valid_count != len(selected):
                raise ValueError("Eine ausgewählte Option ist ungültig.")
            choice_answers[question_id] = selected

        cursor = conn.execute("INSERT INTO submissions (survey_id) VALUES (?)", (survey_id,))
        submission_id = int(cursor.lastrowid)
        for question_id, option_ids in choice_answers.items():
            conn.executemany(
                "INSERT INTO answers (submission_id, question_id, option_id) VALUES (?, ?, ?)",
                [(submission_id, question_id, option_id) for option_id in option_ids],
            )
        conn.executemany(
            "INSERT INTO text_answers (submission_id, question_id, answer_text) VALUES (?, ?, ?)",
            [
                (submission_id, question_id, answer_text)
                for question_id, answer_text in written_answers.items()
            ],
        )
        return submission_id


def survey_results(survey_id: int) -> dict:
    with connection() as conn:
        survey_row = conn.execute(
            "SELECT id, title, created_at, active FROM surveys WHERE id = ?", (survey_id,)
        ).fetchone()
        if not survey_row:
            raise ValueError("Umfrage nicht gefunden.")
        response_count = conn.execute(
            "SELECT COUNT(*) FROM submissions WHERE survey_id = ?", (survey_id,)
        ).fetchone()[0]
        question_rows = conn.execute(
            """SELECT id, question_text, question_type, free_text, position FROM questions
               WHERE survey_id = ? ORDER BY position, id""",
            (survey_id,),
        ).fetchall()
        questions = []
        for question_row in question_rows:
            question = dict(question_row)
            is_text = bool(question.pop("free_text"))
            question["question_type"] = "text" if is_text else question["question_type"]
            if is_text:
                rows = conn.execute(
                    """SELECT ta.answer_text, s.submitted_at
                       FROM text_answers ta
                       JOIN submissions s ON s.id = ta.submission_id
                       WHERE ta.question_id = ? ORDER BY s.submitted_at DESC, ta.id DESC""",
                    (question["id"],),
                ).fetchall()
                question["results"] = []
                question["text_answers"] = [dict(row) for row in rows]
            else:
                rows = conn.execute(
                    """SELECT o.id, o.label, COUNT(a.id) AS votes FROM options o
                       LEFT JOIN answers a ON a.option_id = o.id
                       WHERE o.question_id = ? GROUP BY o.id, o.label, o.position
                       ORDER BY o.position, o.id""",
                    (question["id"],),
                ).fetchall()
                question["results"] = [dict(row) for row in rows]
                question["text_answers"] = []
            questions.append(question)
        result = dict(survey_row)
        result["response_count"] = int(response_count)
        result["questions"] = questions
        return result


def set_survey_active(survey_id: int, active: bool) -> None:
    with connection() as conn:
        conn.execute("UPDATE surveys SET active = ? WHERE id = ?", (int(active), survey_id))
