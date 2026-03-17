import gspread
import os
from dotenv import load_dotenv
from itertools import combinations
import database as db

load_dotenv()

SPREADSHEET_A_ID = os.getenv("SPREADSHEET_A_ID")
SPREADSHEET_B_ID = os.getenv("SPREADSHEET_B_ID")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")


def get_client():
    if not GOOGLE_CREDENTIALS_FILE or not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        return None
    try:
        client = gspread.service_account(filename=GOOGLE_CREDENTIALS_FILE)
        return client
    except Exception as e:
        print(f"Auth error: {e}")
        return None


def sync_questions():
    """Reads Spreadsheet A and returns formatted questions for SQLite."""
    client = get_client()
    if not client:
        return []
    try:
        sheet_a = client.open_by_key(SPREADSHEET_A_ID).sheet1
        records = sheet_a.get_all_records()
        questions_data = []
        for row in records:
            q_text = row.get("Question_Text")
            if not q_text:
                continue
            options = []
            for key, val in row.items():
                if key.startswith("Option_") and val and str(val).strip():
                    options.append(str(val).strip())
            questions_data.append({"text": q_text, "options": options})
        return questions_data
    except Exception as e:
        print(f"Error syncing questions: {e}")
        return []


def _ensure_b_client():
    client = get_client()
    if not client:
        return None, None
    try:
        b_sheet = client.open_by_key(SPREADSHEET_B_ID)
        return client, b_sheet
    except Exception as e:
        print(f"Error opening Spreadsheet B: {e}")
        return None, None


def sync_answers(questions):
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return []
    answers_data = []
    for idx, q in enumerate(questions):
        q_tab_name = f"Вопрос {idx + 1}"
        try:
            ws = b_sheet.worksheet(q_tab_name)
            rows = ws.get_all_values()
            if len(rows) > 2:
                for row in rows[2:]:
                    if len(row) >= 3:
                        username = row[1].strip()
                        answer_text = row[2].strip()
                        if username and answer_text:
                            answers_data.append(
                                {
                                    "username": username,
                                    "question_text": q["text"],
                                    "answer_text": answer_text,
                                }
                            )
        except gspread.exceptions.WorksheetNotFound:
            pass
        except Exception as e:
            print(f"Ошибка синхронизации ответов для {q_tab_name}: {e}")
    return answers_data


def update_user_roster():
    """Writes all users to 'Users' tab in Spreadsheet B."""
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return

    try:
        ws = b_sheet.worksheet("Пользователи")
    except gspread.exceptions.WorksheetNotFound:
        ws = b_sheet.add_worksheet(title="Пользователи", rows="100", cols="10")
        ws.append_row(["ID пользователя", "Имя", "Username", "Пол", "Ищет", "Статус"])

    users = db.get_all_users()
    rows = [["ID пользователя", "Имя", "Username", "Пол", "Ищет", "Статус"]]
    for user in users:
        rows.append(
            [
                user["user_id"],
                user["name"],
                user["username"],
                user["gender"],
                user["looking_for"],
                user.get("status", ""),
            ]
        )

    ws.clear()
    ws.update(rows)


def append_answer_to_q_sheet(user, question, answer_text):
    """Appends an answer to 'Q#' tab."""
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return

    # We will just generate a tab name based on a sanitized version or simply use an index if we tracked it,
    # but the requirement states "One tab per question". Since we removed ID, let's use a snippet of the text
    # or enumerate them from the sorted questions.
    all_questions = db.get_all_questions()
    q_index = 1
    for idx, q in enumerate(all_questions):
        if q["text"] == question["text"]:
            q_index = idx + 1
            break

    q_tab_name = f"Вопрос {q_index}"
    try:
        ws = b_sheet.worksheet(q_tab_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = b_sheet.add_worksheet(title=q_tab_name, rows="100", cols="10")
        ws.append_row([question["text"]])
        ws.append_row(["Имя", "Username", "Ответ"])

    ws.append_row([user["name"], user["username"], answer_text])


def recalculate_top_matches():
    """Calculates top matches and writes to Spreadsheet B."""
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return

    users = db.get_all_users()
    answers = db.get_all_answers()

    # Map user id to answers
    user_answers = {}
    for a in answers:
        uid = a["user_id"]
        if uid not in user_answers:
            user_answers[uid] = {}
        user_answers[uid][a["question_text"]] = a["answer_text"]

    completed_users = [u for u in users if u.get("status") == "complete"]

    # Calculate all pairwise filtered matches
    # We need to map user_id -> list of matches
    all_user_matches = {u["user_id"]: [] for u in completed_users}

    all_questions = db.get_all_questions()
    q_map = {q["text"]: i + 1 for i, q in enumerate(all_questions)}

    for u1, u2 in combinations(completed_users, 2):
        a1 = user_answers.get(u1["user_id"], {})
        a2 = user_answers.get(u2["user_id"], {})

        shared_q = set(a1.keys()).intersection(set(a2.keys()))
        match_count = 0
        matched_q_indices = []
        for qtext in shared_q:
            if a1[qtext] == a2[qtext]:
                match_count += 1
                if qtext in q_map:
                    matched_q_indices.append(str(q_map[qtext]))

        u1_gender, u1_looking = (
            u1.get("gender", "").lower(),
            u1.get("looking_for", "").lower(),
        )
        u2_gender, u2_looking = (
            u2.get("gender", "").lower(),
            u2.get("looking_for", "").lower(),
        )

        match_1_likes_2 = u1_looking == "all" or u1_looking == u2_gender
        match_2_likes_1 = u2_looking == "all" or u2_looking == u1_gender

        if match_1_likes_2 and match_2_likes_1:
            # Sort indices numerically
            matched_q_indices.sort(key=int)
            shared_q_str = ", ".join(matched_q_indices)
            u1_display = f"{u1['name']} (@{u1['username']})"
            u2_display = f"{u2['name']} (@{u2['username']})"

            all_user_matches[u1["user_id"]].append(
                (u2_display, match_count, shared_q_str)
            )
            all_user_matches[u2["user_id"]].append(
                (u1_display, match_count, shared_q_str)
            )

    # Identify top match for each user for the summary sheet
    top_matches_summary = []

    for u in completed_users:
        uid = u["user_id"]
        u_display = f"{u['name']} (@{u['username']})"
        matches = all_user_matches[uid]
        if matches:
            # Sort by match_count descending
            matches.sort(key=lambda x: x[1], reverse=True)
            top_match = matches[0]
            top_matches_summary.append(
                [u_display, top_match[0], top_match[1], top_match[2]]
            )
        else:
            top_matches_summary.append([u_display, "Нет мэтчей", 0, ""])

    # Write Top Matches Summary
    try:
        ws_summary = b_sheet.worksheet("Топ мэтчей")
    except gspread.exceptions.WorksheetNotFound:
        ws_summary = b_sheet.add_worksheet(title="Топ мэтчей", rows="100", cols="10")

    summary_header = [
        ["Пользователь", "Лучший мэтч", "Совпадений", "Совпавшие вопросы"]
    ]
    ws_summary.clear()
    ws_summary.update(summary_header + top_matches_summary)

    # Write individual user sheets
    for u in completed_users:
        uid = u["user_id"]
        # Limit sheet name length as gspread might throw error on super long names, though 100 character limit usually
        sheet_name = f"Топ {u['name']}"[:100]
        try:
            ws_user = b_sheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws_user = b_sheet.add_worksheet(title=sheet_name, rows="100", cols="10")

        user_header = [["Мэтч", "Совпадений", "Совпавшие вопросы"]]

        matches = all_user_matches[uid]
        # Make sure they are sorted
        matches.sort(key=lambda x: x[1], reverse=True)
        # matches elements are (u2_display, match_count, shared_q_str)
        # convert to list of lists for gspread
        matches_rows = [[m[0], m[1], m[2]] for m in matches]

        ws_user.clear()
        ws_user.update(user_header + matches_rows)


def clear_user_from_sheets(user):
    """Removes user from all rosters and deletes their individual top matches sheet."""
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return

    # 1. Update User Roster (removes from 'Пользователи')
    update_user_roster()

    # 2. Delete individual Top sheet
    try:
        sheet_name = f"Топ {user['name']}"[:100]
        ws_user = b_sheet.worksheet(sheet_name)
        b_sheet.del_worksheet(ws_user)
    except gspread.exceptions.WorksheetNotFound:
        pass
    except Exception as e:
        print(f"Error deleting user sheet: {e}")

    # 3. Recalculate Top Matches (updates 'Топ мэтчей' and other users' sheets)
    recalculate_top_matches()

    # 4. Remove user from 'Вопрос #' sheets
    for ws in b_sheet.worksheets():
        if ws.title.startswith("Вопрос"):
            try:
                # Find by username since it's unique and present in those sheets
                cell = ws.find(user["username"])
                if cell:
                    ws.delete_rows(cell.row)
            except gspread.exceptions.CellNotFound:
                pass
            except Exception as e:
                print(f"Error removing user from {ws.title}: {e}")
