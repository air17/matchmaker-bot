from aiogram.fsm.state import State, StatesGroup

class RegistrationStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_gender = State()
    waiting_for_looking_for = State()

class AnsweringStates(StatesGroup):
    answering_questions = State()
