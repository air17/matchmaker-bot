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
            q_text = row.get('Question_Text')
            if not q_text:
                continue
            options = []
            for key, val in row.items():
                if key.startswith('Option_') and val and str(val).strip():
                    options.append(str(val).strip())
            questions_data.append({
                'text': q_text,
                'options': options
            })
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
        q_tab_name = f"Q{idx + 1}"
        try:
            ws = b_sheet.worksheet(q_tab_name)
            rows = ws.get_all_values()
            if len(rows) > 2:
                for row in rows[2:]:
                    if len(row) >= 3:
                        username = row[1].strip()
                        answer_text = row[2].strip()
                        if username and answer_text:
                            answers_data.append({
                                'username': username,
                                'question_text': q['text'],
                                'answer_text': answer_text
                            })
        except gspread.exceptions.WorksheetNotFound:
            pass
        except Exception as e:
            print(f"Error syncing answers for {q_tab_name}: {e}")
    return answers_data

def update_user_roster():
    """Writes all users to 'Users' tab in Spreadsheet B."""
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return
    
    try:
        ws = b_sheet.worksheet("Users")
    except gspread.exceptions.WorksheetNotFound:
        ws = b_sheet.add_worksheet(title="Users", rows="100", cols="10")
        ws.append_row(["User_ID", "Name", "Username", "Gender", "Looking_For", "Status"])
        
    users = db.get_all_users()
    rows = [["User_ID", "Name", "Username", "Gender", "Looking_For", "Status"]]
    for user in users:
        rows.append([user['user_id'], user['name'], user['username'], user['gender'], user['looking_for'], user.get('status', '')])
        
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
        if q['text'] == question['text']:
            q_index = idx + 1
            break
            
    q_tab_name = f"Q{q_index}"
    try:
        ws = b_sheet.worksheet(q_tab_name)
    except gspread.exceptions.WorksheetNotFound:
        ws = b_sheet.add_worksheet(title=q_tab_name, rows="100", cols="10")
        ws.append_row([question['text']])
        ws.append_row(["Name", "Username", "Answer"])
        
    ws.append_row([user['name'], user['username'], answer_text])

def recalculate_top_matches():
    """Calculates strict and unfiltered matches and writes to Spreadsheet B."""
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return
    
    users = db.get_all_users()
    answers = db.get_all_answers()
    
    # Map user id to answers
    user_answers = {}
    for a in answers:
        uid = a['user_id']
        if uid not in user_answers:
            user_answers[uid] = {}
        user_answers[uid][a['question_text']] = a['answer_text']
        
    filtered_matches = []
    unfiltered_matches = []
    
    for u1, u2 in combinations(users, 2):
        if not u1.get('status') == 'complete' or not u2.get('status') == 'complete':
            continue
            
        # Match count logic
        a1 = user_answers.get(u1['user_id'], {})
        a2 = user_answers.get(u2['user_id'], {})
        
        shared_q = set(a1.keys()).intersection(set(a2.keys()))
        match_count = 0
        matched_q_texts = []
        for qtext in shared_q:
            if a1[qtext] == a2[qtext]:
                match_count += 1
                matched_q_texts.append(qtext[:15] + "...") # Preview instead of Q1
                
        match_record = [
            f"{u1['name']} (@{u1['username']})",
            f"{u2['name']} (@{u2['username']})",
            match_count,
            ", ".join(matched_q_texts)
        ]
        
        # Unfiltered
        unfiltered_matches.append(match_record)
        
        # Filtered logic
        u1_gender, u1_looking = u1.get('gender', '').lower(), u1.get('looking_for', '').lower()
        u2_gender, u2_looking = u2.get('gender', '').lower(), u2.get('looking_for', '').lower()
        
        # Super simple check: 
        match_1_likes_2 = ('all' in u1_looking or u2_gender in u1_looking)
        match_2_likes_1 = ('all' in u2_looking or u1_gender in u2_looking)
        
        if match_1_likes_2 and match_2_likes_1:
            filtered_matches.append(match_record)
            
    # Sort by match count descending
    filtered_matches.sort(key=lambda x: x[2], reverse=True)
    unfiltered_matches.sort(key=lambda x: x[2], reverse=True)
    
    # Write Filtered
    try:
        ws_filtered = b_sheet.worksheet("Top Matches (Filtered)")
    except gspread.exceptions.WorksheetNotFound:
        ws_filtered = b_sheet.add_worksheet(title="Top Matches (Filtered)", rows="100", cols="10")
        
    header = [["User A", "User B", "Match Count", "Matched Questions List"]]
    ws_filtered.clear()
    ws_filtered.update(header + filtered_matches)
    
    # Write Unfiltered
    try:
        ws_unfiltered = b_sheet.worksheet("Top Matches (Unfiltered)")
    except gspread.exceptions.WorksheetNotFound:
        ws_unfiltered = b_sheet.add_worksheet(title="Top Matches (Unfiltered)", rows="100", cols="10")
        
    ws_unfiltered.clear()
    ws_unfiltered.update(header + unfiltered_matches)

def clear_user_from_sheets(user):
    # Overly complex to individually delete rows, simpler to just fully rebuild everything
    update_user_roster()
    recalculate_top_matches()
    # For Q# tabs, we won't purge them immediately without full sync to avoid API hit limits, 
    # but the assignment requests removing them.
    # To do it properly:
    _, b_sheet = _ensure_b_client()
    if not b_sheet:
        return
    for ws in b_sheet.worksheets():
        if ws.title.startswith("Q"):
            try:
                cell = ws.find(user['username'])
                if cell:
                    ws.delete_rows(cell.row)
            except gspread.exceptions.CellNotFound:
                pass
