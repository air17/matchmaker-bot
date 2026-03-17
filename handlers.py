from aiogram import Router, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
import google_sheets as gs
from states import RegistrationStates, AnsweringStates
import asyncio

router = Router()

# ----------------- ADMIN COMMANDS ----------------- #

def ensure_admin(func):
    async def wrapper(message: types.Message, state: FSMContext, **kwargs):
        if not db.is_admin(message.from_user.id):
            await message.reply("Access denied. Admins only.")
            return
        return await func(message, state)
    return wrapper

@router.message(Command("update"))
@ensure_admin
async def cmd_update(message: types.Message, state: FSMContext):
    await message.reply("Syncing questions from Google Sheets...")
    questions = gs.sync_questions()
    if not questions:
        await message.reply("Failed to sync. Check logs or empty sheet.")
        return
    db.sync_questions(questions)
    
    answers_data = gs.sync_answers(questions)
    if answers_data:
        db.sync_answers_from_data(answers_data)
        
    gs.recalculate_top_matches()
    
    await message.reply(f"Successfully synced {len(questions)} questions and {len(answers_data)} answers from sheets.")

@router.message(Command("reset_user"))
@ensure_admin
async def cmd_reset_user(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Usage: /reset_user <@username or user_id>")
        return
        
    target = args[1].strip()
    target_user = None
    users = db.get_all_users()
    for u in users:
        if str(u['user_id']) == target or f"@{u['username']}" == target or u['username'] == target.replace('@',''):
            target_user = u
            break
            
    if not target_user:
        await message.reply("User not found.")
        return
        
    # Delete from DB
    db.delete_user(target_user['user_id'])
    
    # Delete from Sheets
    await message.reply("Removing user from spreadsheet...")
    gs.clear_user_from_sheets(target_user)
    
    await message.reply(f"User {target_user['name']} has been reset.")

@router.message(Command("add_admin"))
@ensure_admin
async def cmd_add_admin(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Usage: /add_admin <user_id> <username>")
        return
    parts = args[1].split()
    if len(parts) < 2:
        await message.reply("Usage: /add_admin <user_id> <username>")
        return
    try:
        user_id = int(parts[0])
        username = parts[1].replace('@', '')
        db.add_admin(user_id, username)
        await message.reply(f"Added admin ID {user_id} (@{username})")
    except ValueError:
        await message.reply("Invalid user ID format.")

@router.message(Command("list_admins"))
@ensure_admin
async def cmd_list_admins(message: types.Message, state: FSMContext):
    admins = db.get_all_admins()
    msg = "Admins:\n" + "\n".join([f"- {a['user_id']} (@{a['username']})" for a in admins])
    await message.reply(msg)

# ----------------- USER FLOW ----------------- #

@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user:
        if user['status'] == 'complete':
            await message.reply("You have already completed the questionnaire. No further action needed.")
            return
            
        if user.get('name') == 'Pending' or not user.get('name'):
            await message.reply("Welcome back! Please enter your real name:")
            await state.set_state(RegistrationStates.waiting_for_name)
            return
        elif not user.get('gender'):
            builder = InlineKeyboardBuilder()
            builder.add(types.InlineKeyboardButton(text="Male", callback_data="gender_male"))
            builder.add(types.InlineKeyboardButton(text="Female", callback_data="gender_female"))
            await message.reply("What is your gender?", reply_markup=builder.as_markup())
            await state.set_state(RegistrationStates.waiting_for_gender)
            return
        elif not user.get('looking_for'):
            builder = InlineKeyboardBuilder()
            builder.add(types.InlineKeyboardButton(text="Men", callback_data="looking_male"))
            builder.add(types.InlineKeyboardButton(text="Women", callback_data="looking_female"))
            builder.add(types.InlineKeyboardButton(text="All/Both", callback_data="looking_all"))
            builder.adjust(2, 1)
            await message.reply("Who are you looking for?", reply_markup=builder.as_markup())
            await state.set_state(RegistrationStates.waiting_for_looking_for)
            return
        else:
            await serve_next_question(message, state, message.from_user.id)
            return
    
    # New user Registration
    if not message.from_user.username:
        await message.reply("Warning: You need a Telegram @username to participate. Please set one in your Telegram profile and try again.")
        return
        
    db.add_or_update_user(user_id=message.from_user.id, name="Pending", username=message.from_user.username, status="registering")
    
    await message.reply("Welcome to Matchmaking! Please enter your real name:")
    await state.set_state(RegistrationStates.waiting_for_name)

@router.message(RegistrationStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    # Update real name
    db.add_or_update_user(user_id=message.from_user.id, name=message.text, username=message.from_user.username)
    gs.update_user_roster()
    
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Male", callback_data="gender_male"))
    builder.add(types.InlineKeyboardButton(text="Female", callback_data="gender_female"))
    
    await message.reply("What is your gender?", reply_markup=builder.as_markup())
    await state.set_state(RegistrationStates.waiting_for_gender)

@router.callback_query(RegistrationStates.waiting_for_gender, F.data.startswith("gender_"))
async def process_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    user = db.get_user(callback.from_user.id)
    db.add_or_update_user(user_id=callback.from_user.id, name=user['name'], username=user['username'], gender=gender)
    gs.update_user_roster()
    
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Men", callback_data="looking_male"))
    builder.add(types.InlineKeyboardButton(text="Women", callback_data="looking_female"))
    builder.add(types.InlineKeyboardButton(text="All/Both", callback_data="looking_all"))
    builder.adjust(2, 1)
    
    await callback.message.edit_text(f"Gender selected: {gender.capitalize()}\nWho are you looking for?", reply_markup=builder.as_markup())
    await state.set_state(RegistrationStates.waiting_for_looking_for)

@router.callback_query(RegistrationStates.waiting_for_looking_for, F.data.startswith("looking_"))
async def process_looking_for(callback: types.CallbackQuery, state: FSMContext):
    looking_for = callback.data.split("_")[1]
    user = db.get_user(callback.from_user.id)
    db.add_or_update_user(user_id=callback.from_user.id, name=user['name'], username=user['username'], gender=user['gender'], looking_for=looking_for, status="answering")
    gs.update_user_roster()
    
    await callback.message.edit_text(f"Looking for: {looking_for.capitalize()}\nExcellent. Let's start the questions.")
    await serve_next_question(callback.message, state, callback.from_user.id)

async def serve_next_question(message: types.Message, state: FSMContext, user_id: int):
    user = db.get_user(user_id)
    if not user or user.get('name') == 'Pending' or not user.get('gender') or not user.get('looking_for'):
        await message.answer("Please complete your profile first! Use /start to register.")
        return

    # Remove callback logic issue, send standard message
    questions = db.get_all_questions()
    if not questions:
        await message.answer("Questions aren't configured yet. Ask the admin to run /update.")
        return
        
    answers = db.get_user_answers(user_id)
    
    next_question = None
    for q in questions:
        if q['text'] not in answers:
            next_question = q
            break
            
    if not next_question:
        # User finished everything
        user = db.get_user(user_id)
        db.add_or_update_user(user_id=user_id, name=user['name'], username=user['username'], gender=user['gender'], looking_for=user['looking_for'], status='complete')
        gs.update_user_roster()
        gs.recalculate_top_matches()
        await message.answer("Congratulations! You have answered all questions.")
        await state.clear()
        return
        
    builder = InlineKeyboardBuilder()
    for idx, opt in enumerate(next_question['options']):
        # Option index is the callback data, keep buttons short (number/emoji usually provided by admin)
        # Using a hash of the text or index of question in the list.
        # Since we just have text now, we'll find its index in the all_questions list to build a short callback
        all_q = db.get_all_questions()
        q_idx = next(i for i, q in enumerate(all_q) if q['text'] == next_question['text'])
        builder.add(types.InlineKeyboardButton(text=str(idx+1), callback_data=f"ans_{q_idx}_{idx}"))
    
    opt_text = "\n".join([f"{i+1}. {o}" for i, o in enumerate(next_question['options'])])
    msg_text = f"<b>{next_question['text']}</b>\n\n{opt_text}"
    
    await message.answer(msg_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(AnsweringStates.answering_questions)

# Removed AnsweringStates state filter so it catches clicks even if bot restarted (MemoryStorage wiped)
@router.callback_query(F.data.startswith("ans_"))
async def process_answer(callback: types.CallbackQuery, state: FSMContext):
    user = db.get_user(callback.from_user.id)
    if not user or user.get('name') == 'Pending' or not user.get('gender') or not user.get('looking_for'):
        await callback.message.answer("Please complete your profile first! Use /start to register.")
        await callback.answer()
        return

    parts = callback.data.split("_")
    q_idx = int(parts[1])
    opt_idx = int(parts[2])
    
    all_questions = db.get_all_questions()
    if q_idx >= len(all_questions):
        await callback.answer("Error getting question.")
        return
        
    question = all_questions[q_idx]
    
    answer_text = question['options'][opt_idx]
    
    db.save_answer(callback.from_user.id, question['text'], answer_text)
    user = db.get_user(callback.from_user.id)
    gs.append_answer_to_q_sheet(user, question, answer_text)
    
    # Recalculate top matches silently in background
    # (Doing this async/non-blocking if possible is better, but doing sync here for simplicity)
    asyncio.create_task(asyncio.to_thread(gs.recalculate_top_matches))
    
    # Send chosen feedback shortly and load next
    await callback.answer()  # Clear the button spinner
    await callback.message.edit_text(f"<i>Question '{question['text'][:15]}...' answered: {answer_text}</i>", parse_mode="HTML")
    await serve_next_question(callback.message, state, callback.from_user.id)

@router.message(lambda message: True)
async def catch_all(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user and user['status'] == 'complete':
        # Ignore
        pass
    else:
        await message.reply("Please use the inline buttons or type /start to register.")

# Fallback: silently answer any stale / unrecognised callback queries so
# Telegram doesn't show a "loading" spinner forever on old inline buttons.
@router.callback_query()
async def catch_all_callbacks(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("This button is no longer active.", show_alert=False)
