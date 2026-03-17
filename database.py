import sqlite3
import json
from contextlib import contextmanager

DB_FILE = "matchmaking.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        # Admins table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT
            )
        """)

        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                name TEXT,
                username TEXT,
                gender TEXT,
                looking_for TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)

        # Questions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS questions (
                question_text TEXT PRIMARY KEY,
                options TEXT,
                is_unlocked INTEGER DEFAULT 0
            )
        """)
        # For existing tables, add the column if it doesn't exist
        try:
            cursor.execute(
                "ALTER TABLE questions ADD COLUMN is_unlocked INTEGER DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Answers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS answers (
                user_id INTEGER,
                question_text TEXT,
                answer_text TEXT,
                PRIMARY KEY (user_id, question_text),
                FOREIGN KEY (user_id) REFERENCES users (user_id),
                FOREIGN KEY (question_text) REFERENCES questions (question_text)
            )
        """)


def add_admin(user_id, username):
    with get_db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)",
            (user_id, username),
        )


def is_admin(user_id):
    with get_db() as conn:
        res = conn.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(res)


def get_all_admins():
    with get_db() as conn:
        res = conn.execute("SELECT user_id, username FROM admins").fetchall()
        return [{"user_id": r["user_id"], "username": r["username"]} for r in res]


def add_or_update_user(
    user_id, name, username, gender=None, looking_for=None, status="pending"
):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()

        if user:
            # Update
            query = "UPDATE users SET name = ?, username = ?"
            params = [name, username]
            if gender is not None:
                query += ", gender = ?"
                params.append(gender)
            if looking_for is not None:
                query += ", looking_for = ?"
                params.append(looking_for)
            if status is not None:
                query += ", status = ?"
                params.append(status)
            query += " WHERE user_id = ?"
            params.append(user_id)
            cursor.execute(query, tuple(params))
        else:
            # Insert
            cursor.execute(
                "INSERT INTO users (user_id, name, username, gender, looking_for, status) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, name, username, gender, looking_for, status),
            )


def get_user(user_id):
    with get_db() as conn:
        res = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return dict(res) if res else None


def get_all_users():
    with get_db() as conn:
        res = conn.execute("SELECT * FROM users").fetchall()
        return [dict(r) for r in res]


def delete_user(user_id):
    with get_db() as conn:
        conn.execute("DELETE FROM answers WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))


def sync_questions(questions_data):
    """
    questions_data: list of dicts: [{'text': 'Q1', 'options': ['A', 'B']}]
    """
    with get_db() as conn:
        cursor = conn.cursor()
        incoming_texts = [q["text"] for q in questions_data]

        for q in questions_data:
            # We use INSERT OR IGNORE then UPDATE to avoid overwriting is_unlocked if it already exists
            cursor.execute(
                "INSERT OR IGNORE INTO questions (question_text, options, is_unlocked) VALUES (?, ?, 0)",
                (q["text"], json.dumps(q["options"])),
            )
            cursor.execute(
                "UPDATE questions SET options = ? WHERE question_text = ?",
                (json.dumps(q["options"]), q["text"]),
            )

        cursor.execute("SELECT question_text FROM questions")
        existing_texts = [row["question_text"] for row in cursor.fetchall()]

        for text in existing_texts:
            if text not in incoming_texts:
                cursor.execute("DELETE FROM answers WHERE question_text = ?", (text,))
                cursor.execute("DELETE FROM questions WHERE question_text = ?", (text,))


def get_all_questions():
    with get_db() as conn:
        res = conn.execute("SELECT * FROM questions").fetchall()
        return [
            {
                "text": r["question_text"],
                "options": json.loads(r["options"]),
                "is_unlocked": bool(r["is_unlocked"]),
            }
            for r in res
        ]


def get_unlocked_questions():
    with get_db() as conn:
        res = conn.execute(
            "SELECT * FROM questions WHERE is_unlocked = 1 ORDER BY ROWID"
        ).fetchall()
        return [
            {"text": r["question_text"], "options": json.loads(r["options"])}
            for r in res
        ]


def unlock_next_question():
    with get_db() as conn:
        cursor = conn.cursor()
        # Find first locked question
        res = cursor.execute(
            "SELECT question_text FROM questions WHERE is_unlocked = 0 ORDER BY ROWID LIMIT 1"
        ).fetchone()
        if res:
            q_text = res["question_text"]
            cursor.execute(
                "UPDATE questions SET is_unlocked = 1 WHERE question_text = ?",
                (q_text,),
            )
            return q_text
        return None


def lock_all_questions():
    with get_db() as conn:
        conn.execute("UPDATE questions SET is_unlocked = 0")


def has_more_locked_questions():
    with get_db() as conn:
        res = conn.execute(
            "SELECT 1 FROM questions WHERE is_unlocked = 0 LIMIT 1"
        ).fetchone()
        return bool(res)


def sync_answers_from_data(answers_data):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM users")
        user_map = {row["username"]: row["user_id"] for row in cursor.fetchall()}

        for a in answers_data:
            username = a["username"]
            if username in user_map:
                user_id = user_map[username]
                cursor.execute(
                    "INSERT OR REPLACE INTO answers (user_id, question_text, answer_text) VALUES (?, ?, ?)",
                    (user_id, a["question_text"], a["answer_text"]),
                )


def get_question(question_text):
    with get_db() as conn:
        res = conn.execute(
            "SELECT * FROM questions WHERE question_text = ?", (question_text,)
        ).fetchone()
        return (
            {"text": res["question_text"], "options": json.loads(res["options"])}
            if res
            else None
        )


def save_answer(user_id, question_text, answer_text):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO answers (user_id, question_text, answer_text) VALUES (?, ?, ?)",
            (user_id, question_text, answer_text),
        )


def get_user_answers(user_id):
    with get_db() as conn:
        res = conn.execute(
            "SELECT question_text, answer_text FROM answers WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {r["question_text"]: r["answer_text"] for r in res}


def get_all_answers():
    with get_db() as conn:
        res = conn.execute("SELECT * FROM answers").fetchall()
        return [dict(r) for r in res]
