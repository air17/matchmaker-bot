import gspread
import gspread_asyncio
import os
from dotenv import load_dotenv
from itertools import combinations
from google.oauth2.service_account import Credentials
import database as db

load_dotenv()

SPREADSHEET_A_ID = os.getenv("SPREADSHEET_A_ID")
SPREADSHEET_B_ID = os.getenv("SPREADSHEET_B_ID")
SPREADSHEET_C_ID = os.getenv("SPREADSHEET_C_ID")
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE")


def get_creds():
    creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE)
    scoped = creds.with_scopes(
        [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
    )
    return scoped


agcm = gspread_asyncio.AsyncioGspreadClientManager(get_creds)


async def get_client():
    if not GOOGLE_CREDENTIALS_FILE or not os.path.exists(GOOGLE_CREDENTIALS_FILE):
        return None
    try:
        client = await agcm.authorize()
        return client
    except Exception as e:
        print(f"Auth error: {e}")
        return None


async def sync_questions():
    """Reads Spreadsheet A and returns formatted questions for SQLite."""
    client = await get_client()
    if not client:
        return []
    try:
        sheet_a = await client.open_by_key(SPREADSHEET_A_ID)
        ws = await sheet_a.get_worksheet(0)
        records = await ws.get_all_records()
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


async def _ensure_b_client():
    client = await get_client()
    if not client:
        return None, None
    try:
        b_sheet = await client.open_by_key(SPREADSHEET_B_ID)
        return client, b_sheet
    except Exception as e:
        print(f"Error opening Spreadsheet B: {e}")
        return None, None


async def _ensure_c_client():
    client = await get_client()
    if not client:
        return None, None

    try:
        c_sheet = await client.open_by_key(SPREADSHEET_C_ID)
        return client, c_sheet
    except Exception as e:
        print(f"Error opening Spreadsheet C: {e}")
        return None, None


async def sync_answers(questions):
    _, b_sheet = await _ensure_b_client()
    if not b_sheet:
        return []
    answers_data = []
    for idx, q in enumerate(questions):
        q_tab_name = f"Вопрос {idx + 1}"
        try:
            ws = await b_sheet.worksheet(q_tab_name)
            rows = await ws.get_all_values()
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


async def update_user_roster():
    """Writes all users to 'Users' tab in Spreadsheet B."""
    _, b_sheet = await _ensure_b_client()
    if not b_sheet:
        return

    try:
        ws = await b_sheet.worksheet("Пользователи")
    except gspread.exceptions.WorksheetNotFound:
        ws = await b_sheet.add_worksheet(title="Пользователи", rows=100, cols=10)
        await ws.append_row(
            ["ID пользователя", "Имя", "Username", "Пол", "Ищет", "Статус"]
        )

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

    await ws.clear()
    await ws.update(rows)


async def ensure_question_sheets(questions):
    """Ensures that each question has a corresponding tab in Spreadsheet B."""
    _, b_sheet = await _ensure_b_client()
    if not b_sheet:
        return

    worksheets = await b_sheet.worksheets()
    ws_map = {ws.title: ws for ws in worksheets}

    formatting_requests = []
    for idx, q in enumerate(questions):
        q_tab_name = f"Вопрос {idx + 1}"
        if q_tab_name not in ws_map:
            ws = await b_sheet.add_worksheet(title=q_tab_name, rows=100, cols=3)
            await ws.append_row([q["text"]])
            await ws.append_row(["Имя", "Username", "Ответ"])
        else:
            ws = ws_map[q_tab_name]
            first_row = await ws.row_values(1)
            if not first_row or first_row[0] != q["text"]:
                await ws.update_cell(1, 1, q["text"])

        # Add formatting requests for this question sheet
        formatting_requests.extend(
            [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": ws.id,
                            "startRowIndex": 0,
                            "endRowIndex": 1,
                            "startColumnIndex": 0,
                            "endColumnIndex": 1,
                        },
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold",
                    }
                },
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": ws.id,
                            "startColumnIndex": 2,
                            "endColumnIndex": 3,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "backgroundColor": {
                                    "red": 0.92,
                                    "green": 1.0,
                                    "blue": 0.92,
                                }
                            }
                        },
                        "fields": "userEnteredFormat.backgroundColor",
                    }
                },
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "COLUMNS",
                            "startIndex": 2,
                            "endIndex": 3,
                        },
                        "properties": {"pixelSize": 1000},
                        "fields": "pixelSize",
                    }
                },
            ]
        )

    if formatting_requests:
        try:
            await b_sheet.batch_update({"requests": formatting_requests})
        except Exception as e:
            print(f"Error applying batch formatting: {e}")

    # Cleanup stale sheets
    num_questions = len(questions)
    for ws_title, ws in ws_map.items():
        if ws_title.startswith("Вопрос "):
            try:
                title_parts = ws_title.split()
                if len(title_parts) == 2:
                    q_num = int(title_parts[1])
                    if q_num > num_questions:
                        await b_sheet.del_worksheet(ws)
            except (ValueError, IndexError):
                pass


async def append_answer_to_q_sheet(user, question, answer_text):
    """Appends an answer to 'Q#' tab."""
    _, b_sheet = await _ensure_b_client()
    if not b_sheet:
        return

    all_questions = db.get_all_questions()
    q_index = 1
    for idx, q in enumerate(all_questions):
        if q["text"] == question["text"]:
            q_index = idx + 1
            break

    q_tab_name = f"Вопрос {q_index}"
    try:
        ws = await b_sheet.worksheet(q_tab_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = await b_sheet.add_worksheet(title=q_tab_name, rows=100, cols=10)
        await ws.append_row([question["text"]])
        await ws.append_row(["Имя", "Username", "Ответ"])
        # Format the new sheet
        try:
            await b_sheet.batch_update(
                {
                    "requests": [
                        {
                            "repeatCell": {
                                "range": {
                                    "sheetId": ws.id,
                                    "startRowIndex": 0,
                                    "endRowIndex": 1,
                                    "startColumnIndex": 0,
                                    "endColumnIndex": 1,
                                },
                                "cell": {
                                    "userEnteredFormat": {"textFormat": {"bold": True}}
                                },
                                "fields": "userEnteredFormat.textFormat.bold",
                            }
                        },
                        {
                            "repeatCell": {
                                "range": {
                                    "sheetId": ws.id,
                                    "startColumnIndex": 2,
                                    "endColumnIndex": 3,
                                },
                                "cell": {
                                    "userEnteredFormat": {
                                        "backgroundColor": {
                                            "red": 0.92,
                                            "green": 1.0,
                                            "blue": 0.92,
                                        }
                                    }
                                },
                                "fields": "userEnteredFormat.backgroundColor",
                            }
                        },
                        {
                            "updateDimensionProperties": {
                                "range": {
                                    "sheetId": ws.id,
                                    "dimension": "COLUMNS",
                                    "startIndex": 2,
                                    "endIndex": 3,
                                },
                                "properties": {"pixelSize": 1000},
                                "fields": "pixelSize",
                            }
                        },
                    ]
                }
            )
        except Exception as e:
            print(f"Error formatting new worksheet: {e}")

    await ws.append_row([user["name"], user["username"], answer_text])


async def recalculate_top_matches():
    """Calculates top matches and writes to Spreadsheet C (results spreadsheet)."""
    _, c_sheet = await _ensure_c_client()
    if not c_sheet:
        return

    users = db.get_all_users()
    answers = db.get_all_answers()

    user_answers = {}
    for a in answers:
        uid = a["user_id"]
        if uid not in user_answers:
            user_answers[uid] = {}
        user_answers[uid][a["question_text"]] = a["answer_text"]

    completed_users = [u for u in users if u.get("status") == "complete"]

    all_user_matches = {u["user_id"]: [] for u in completed_users}
    filtered_user_matches = {u["user_id"]: [] for u in completed_users}

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

        matched_q_indices.sort(key=int)
        shared_q_str = ", ".join(matched_q_indices)
        u1_display = f"{u1['name']} (@{u1['username']})"
        u2_display = f"{u2['name']} (@{u2['username']})"

        all_user_matches[u1["user_id"]].append((u2_display, match_count, shared_q_str))
        all_user_matches[u2["user_id"]].append((u1_display, match_count, shared_q_str))

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
            filtered_user_matches[u1["user_id"]].append(
                (u2_display, match_count, shared_q_str)
            )
            filtered_user_matches[u2["user_id"]].append(
                (u1_display, match_count, shared_q_str)
            )

    top_matches_summary = []
    for u in completed_users:
        uid = u["user_id"]
        u_display = f"{u['name']} (@{u['username']})"
        matches = filtered_user_matches[uid]
        if matches:
            matches.sort(key=lambda x: x[1], reverse=True)
            top_match = matches[0]
            top_matches_summary.append(
                [u_display, top_match[0], top_match[1], top_match[2]]
            )
        else:
            top_matches_summary.append([u_display, "Нет мэтчей", 0, ""])

    try:
        ws_summary = await c_sheet.worksheet("Топ мэтчей")
    except gspread.exceptions.WorksheetNotFound:
        ws_summary = await c_sheet.add_worksheet(title="Топ мэтчей", rows=100, cols=10)

    summary_header = [
        ["Пользователь", "Лучший мэтч", "Совпадений", "Совпавшие вопросы"]
    ]
    await ws_summary.clear()
    await ws_summary.update(summary_header + top_matches_summary)

    for u in completed_users:
        uid = u["user_id"]
        sheet_name = f"Топ {u['name']}"[:100]
        try:
            ws_user = await c_sheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws_user = await c_sheet.add_worksheet(title=sheet_name, rows=100, cols=10)

        user_header = [["Мэтч", "Совпадений", "Совпавшие вопросы"]]
        matches = all_user_matches[uid]
        matches.sort(key=lambda x: x[1], reverse=True)
        matches_rows = [[m[0], m[1], m[2]] for m in matches]

        await ws_user.clear()
        await ws_user.update(user_header + matches_rows)


async def clear_user_from_sheets(user):
    """Removes user from all rosters and deletes their individual top matches sheet."""
    _, b_sheet = await _ensure_b_client()
    if not b_sheet:
        return

    await update_user_roster()

    try:
        _, c_sheet = await _ensure_c_client()
        if c_sheet:
            sheet_name = f"Топ {user['name']}"[:100]
            ws_user = await c_sheet.worksheet(sheet_name)
            await c_sheet.del_worksheet(ws_user)
    except gspread.exceptions.WorksheetNotFound:
        pass
    except Exception as e:
        print(f"Error deleting user sheet: {e}")

    await recalculate_top_matches()

    worksheets = await b_sheet.worksheets()
    for ws in worksheets:
        if ws.title.startswith("Вопрос"):
            try:
                cell = await ws.find(user["username"])
                if cell:
                    await ws.delete_rows(cell.row)
            except gspread.exceptions.CellNotFound:
                pass
            except Exception as e:
                print(f"Error removing user from {ws.title}: {e}")
