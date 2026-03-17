# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the bot
python bot.py

# Lint and format (ruff via pre-commit)
pre-commit run --all-files

# Manually lint / format
ruff check --fix .
ruff format .

# Test Google Sheets question sync (requires credentials.json and .env)
python test_sheet.py
```

## Environment Setup

Copy `.env.example` to `.env` and fill in:
- `BOT_TOKEN` — Telegram bot token
- `GOOGLE_CREDENTIALS_FILE` — path to Google service account JSON (default: `credentials.json`)
- `SPREADSHEET_A_ID` — Google Sheet ID for questions input
- `SPREADSHEET_B_ID` — Google Sheet ID for answers/results output
- `INITIAL_ADMIN_ID` / `INITIAL_ADMIN_USERNAME` — seeded on startup

## Architecture

The bot is a Telegram matchmaking bot (Russian-language UI) built with **aiogram 3** and backed by **SQLite** + **Google Sheets**.

### Data flow

1. **Spreadsheet A** (`SPREADSHEET_A_ID`) is read-only: it holds questions with columns `Question_Text`, `Option_1`, `Option_2`, … Admin runs `/update` to sync these into SQLite.
2. **Spreadsheet B** (`SPREADSHEET_B_ID`) is written to by the bot:
   - `Пользователи` tab — user roster
   - `Вопрос N` tabs — one per question, appended with each user answer
   - `Топ мэтчей` tab — summary of each user's best match
   - `Топ <name>` tab — per-user ranked match list
3. SQLite (`matchmaking.db`) is the source of truth at runtime; Sheets are a mirror/output layer.

### Module responsibilities

| File | Role |
|---|---|
| `bot.py` | Entry point: initializes DB, seeds admin, starts aiogram polling |
| `database.py` | All SQLite access via a `get_db()` context manager |
| `google_sheets.py` | All gspread operations; expensive sheet writes are called via `asyncio.to_thread` from handlers |
| `handlers.py` | Single aiogram `Router` with all user-facing and admin commands |
| `states.py` | FSM state groups: `RegistrationStates` and `AnsweringStates` |

### User registration flow (FSM)

`/start` → name → gender (inline button) → looking_for (inline button) → questions loop → `status="complete"`

User statuses: `pending` → `registering` → `answering` → `complete`

### Match calculation

`recalculate_top_matches()` in `google_sheets.py` computes all pairwise matches among `status="complete"` users. Matching is mutual: both users' `looking_for` must be compatible with the other's gender. Score = count of identical answers on shared questions.

### Admin commands

| Command | Description |
|---|---|
| `/update` | Sync questions + answers from Sheets, recalculate matches |
| `/reset_user <@username\|id>` | Delete user from DB and Sheets, recalculate matches |
| `/add_admin <user_id> <username>` | Grant admin privileges |
| `/list_admins` | List current admins |

### Notes

- FSM uses `MemoryStorage` (state is lost on restart). Answer callbacks (`ans_<q_idx>_<opt_idx>`) deliberately omit the FSM state filter so users can still answer after a bot restart.
- The `ensure_admin` decorator is a plain function wrapper, not `functools.wraps` — handler introspection by aiogram must go through the inner `wrapper` name.
