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
            await message.reply("Доступ запрещён. Только для администраторов.")
            return
        return await func(message, state)

    return wrapper


@router.message(Command("update"))
@ensure_admin
async def cmd_update(message: types.Message, state: FSMContext):
    await message.reply("Синхронизация вопросов из Google Sheets...")
    questions = await gs.sync_questions()
    if not questions:
        await message.reply(
            "Не удалось синхронизировать. Проверьте логи или таблицу пуста."
        )
        return
    db.sync_questions(questions)

    answers_data = await gs.sync_answers(questions)
    if answers_data:
        db.sync_answers_from_data(answers_data)

    await gs.recalculate_top_matches()

    await message.reply(
        f"Успешно синхронизировано {len(questions)} вопросов и {len(answers_data)} ответов из таблиц."
    )


@router.message(Command("reset_user"))
@ensure_admin
async def cmd_reset_user(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Использование: /reset_user <@username или user_id>")
        return

    target = args[1].strip()
    target_user = None
    users = db.get_all_users()
    for u in users:
        if (
            str(u["user_id"]) == target
            or f"@{u['username']}" == target
            or u["username"] == target.replace("@", "")
        ):
            target_user = u
            break

    if not target_user:
        await message.reply("Пользователь не найден.")
        return

    # Delete from DB
    db.delete_user(target_user["user_id"])

    # Delete from Sheets
    await message.reply(
        f"Пользователь {target_user['name']} удалён из базы. Очистка Google Sheets..."
    )

    await gs.clear_user_from_sheets(target_user)

    await message.reply(
        f"Готово! Пользователь {target_user['name']} полностью удалён из базы и таблиц. Топ мэтчей обновлён."
    )


@router.message(Command("add_admin"))
@ensure_admin
async def cmd_add_admin(message: types.Message, state: FSMContext):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Использование: /add_admin <user_id> <username>")
        return
    parts = args[1].split()
    if len(parts) < 2:
        await message.reply("Использование: /add_admin <user_id> <username>")
        return
    try:
        user_id = int(parts[0])
        username = parts[1].replace("@", "")
        db.add_admin(user_id, username)
        await message.reply(f"Администратор добавлен: ID {user_id} (@{username})")
    except ValueError:
        await message.reply("Неверный формат user ID.")


@router.message(Command("list_admins"))
@ensure_admin
async def cmd_list_admins(message: types.Message, state: FSMContext):
    admins = db.get_all_admins()
    msg = "Администраторы:\n" + "\n".join(
        [f"- {a['user_id']} (@{a['username']})" for a in admins]
    )
    await message.reply(msg)


# ----------------- USER FLOW ----------------- #


@router.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user:
        if user["status"] == "complete":
            await message.reply(
                "Вы уже завершили анкету. Дальнейших действий не требуется."
            )
            return

        if user.get("name") == "Pending" or not user.get("name"):
            await message.reply(
                "С возвращением! Пожалуйста, введите ваше настоящее имя:"
            )
            await state.set_state(RegistrationStates.waiting_for_name)
            return
        elif not user.get("gender"):
            builder = InlineKeyboardBuilder()
            builder.add(
                types.InlineKeyboardButton(text="Мужской", callback_data="gender_male")
            )
            builder.add(
                types.InlineKeyboardButton(
                    text="Женский", callback_data="gender_female"
                )
            )
            await message.reply("Ваш пол?", reply_markup=builder.as_markup())
            await state.set_state(RegistrationStates.waiting_for_gender)
            return
        elif not user.get("looking_for"):
            builder = InlineKeyboardBuilder()
            builder.add(
                types.InlineKeyboardButton(text="Мужчин", callback_data="looking_male")
            )
            builder.add(
                types.InlineKeyboardButton(
                    text="Женщин", callback_data="looking_female"
                )
            )
            builder.add(
                types.InlineKeyboardButton(text="Всех", callback_data="looking_all")
            )
            builder.adjust(2, 1)
            await message.reply("Кого вы ищете?", reply_markup=builder.as_markup())
            await state.set_state(RegistrationStates.waiting_for_looking_for)
            return
        else:
            await serve_next_question(message, state, message.from_user.id)
            return

    # New user Registration
    if not message.from_user.username:
        await message.reply(
            "Внимание: для участия необходим Telegram @username. Пожалуйста, установите его в профиле Telegram и попробуйте снова."
        )
        return

    db.add_or_update_user(
        user_id=message.from_user.id,
        name="Pending",
        username=message.from_user.username,
        status="registering",
    )

    await message.reply(
        "Добро пожаловать в Мэтчмейкинг! Пожалуйста, введите ваше настоящее имя:"
    )
    await state.set_state(RegistrationStates.waiting_for_name)


@router.message(RegistrationStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    # Update real name
    db.add_or_update_user(
        user_id=message.from_user.id,
        name=message.text,
        username=message.from_user.username,
    )
    await gs.update_user_roster()

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Мужской", callback_data="gender_male"))
    builder.add(
        types.InlineKeyboardButton(text="Женский", callback_data="gender_female")
    )

    await message.reply("Ваш пол?", reply_markup=builder.as_markup())
    await state.set_state(RegistrationStates.waiting_for_gender)


@router.callback_query(
    RegistrationStates.waiting_for_gender, F.data.startswith("gender_")
)
async def process_gender(callback: types.CallbackQuery, state: FSMContext):
    gender = callback.data.split("_")[1]
    user = db.get_user(callback.from_user.id)
    db.add_or_update_user(
        user_id=callback.from_user.id,
        name=user["name"],
        username=user["username"],
        gender=gender,
    )
    await gs.update_user_roster()

    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(text="Мужчин", callback_data="looking_male"))
    builder.add(
        types.InlineKeyboardButton(text="Женщин", callback_data="looking_female")
    )
    builder.add(types.InlineKeyboardButton(text="Всех", callback_data="looking_all"))
    builder.adjust(2, 1)

    await callback.message.edit_text(
        f"Пол выбран: {gender.capitalize()}\nКого вы ищете?",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(RegistrationStates.waiting_for_looking_for)


@router.callback_query(
    RegistrationStates.waiting_for_looking_for, F.data.startswith("looking_")
)
async def process_looking_for(callback: types.CallbackQuery, state: FSMContext):
    looking_for = callback.data.split("_")[1]
    user = db.get_user(callback.from_user.id)
    db.add_or_update_user(
        user_id=callback.from_user.id,
        name=user["name"],
        username=user["username"],
        gender=user["gender"],
        looking_for=looking_for,
        status="answering",
    )
    await gs.update_user_roster()

    await callback.message.edit_text(
        f"Ищу: {looking_for.capitalize()}\nОтлично. Начинаем вопросы."
    )
    await serve_next_question(callback.message, state, callback.from_user.id)


async def serve_next_question(message: types.Message, state: FSMContext, user_id: int):
    user = db.get_user(user_id)
    if (
        not user
        or user.get("name") == "Pending"
        or not user.get("gender")
        or not user.get("looking_for")
    ):
        await message.answer(
            "Пожалуйста, сначала заполните профиль! Используйте /start для регистрации."
        )
        return

    # Remove callback logic issue, send standard message
    questions = db.get_all_questions()
    if not questions:
        await message.answer(
            "Вопросы ещё не настроены. Попросите администратора выполнить /update."
        )
        return

    answers = db.get_user_answers(user_id)

    next_question = None
    for q in questions:
        if q["text"] not in answers:
            next_question = q
            break

    if not next_question:
        # User finished everything
        user = db.get_user(user_id)
        db.add_or_update_user(
            user_id=user_id,
            name=user["name"],
            username=user["username"],
            gender=user["gender"],
            looking_for=user["looking_for"],
            status="complete",
        )
        asyncio.create_task(gs.update_user_roster())
        asyncio.create_task(gs.recalculate_top_matches())
        await message.answer("Поздравляем! Вы ответили на все вопросы.")
        await state.clear()
        return

    builder = InlineKeyboardBuilder()
    for idx, opt in enumerate(next_question["options"]):
        # Option index is the callback data, keep buttons short (number/emoji usually provided by admin)
        # Using a hash of the text or index of question in the list.
        # Since we just have text now, we'll find its index in the all_questions list to build a short callback
        all_q = db.get_all_questions()
        q_idx = next(
            i for i, q in enumerate(all_q) if q["text"] == next_question["text"]
        )
        builder.add(
            types.InlineKeyboardButton(
                text=str(idx + 1), callback_data=f"ans_{q_idx}_{idx}"
            )
        )

    opt_text = "\n".join(
        [f"{i + 1}. {o}" for i, o in enumerate(next_question["options"])]
    )
    msg_text = f"<b>{next_question['text']}</b>\n\n{opt_text}"

    await message.answer(msg_text, reply_markup=builder.as_markup(), parse_mode="HTML")
    await state.set_state(AnsweringStates.answering_questions)


# Removed AnsweringStates state filter so it catches clicks even if bot restarted (MemoryStorage wiped)
@router.callback_query(F.data.startswith("ans_"))
async def process_answer(callback: types.CallbackQuery, state: FSMContext):
    user = db.get_user(callback.from_user.id)
    if (
        not user
        or user.get("name") == "Pending"
        or not user.get("gender")
        or not user.get("looking_for")
    ):
        await callback.message.answer(
            "Пожалуйста, сначала заполните профиль! Используйте /start для регистрации."
        )
        await callback.answer()
        return

    parts = callback.data.split("_")
    q_idx = int(parts[1])
    opt_idx = int(parts[2])

    all_questions = db.get_all_questions()
    if q_idx >= len(all_questions):
        await callback.answer("Ошибка при получении вопроса.")
        return

    question = all_questions[q_idx]

    answer_text = question["options"][opt_idx]

    db.save_answer(callback.from_user.id, question["text"], answer_text)
    user = db.get_user(callback.from_user.id)
    await gs.append_answer_to_q_sheet(user, question, answer_text)

    # Send chosen feedback shortly and load next
    await callback.answer()  # Clear the button spinner
    await callback.message.edit_text(
        f"<i>Ответ на вопрос '{question['text'][:15]}...': {answer_text}</i>",
        parse_mode="HTML",
    )
    await serve_next_question(callback.message, state, callback.from_user.id)


@router.message(lambda message: True)
async def catch_all(message: types.Message, state: FSMContext):
    user = db.get_user(message.from_user.id)
    if user and user["status"] == "complete":
        # Ignore
        pass
    else:
        await message.reply(
            "Пожалуйста, используйте кнопки или введите /start для регистрации."
        )


# Fallback: silently answer any stale / unrecognised callback queries so
# Telegram doesn't show a "loading" spinner forever on old inline buttons.
@router.callback_query()
async def catch_all_callbacks(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("Эта кнопка больше не активна.", show_alert=False)
