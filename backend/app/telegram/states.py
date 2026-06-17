from aiogram.fsm.state import State, StatesGroup


class ProjectCreationState(StatesGroup):
    waiting_for_name = State()
    waiting_for_type = State()
    waiting_for_stage = State()

