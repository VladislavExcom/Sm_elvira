from aiogram.fsm.state import State, StatesGroup


class OrderStates(StatesGroup):
    product = State()
    brand = State()
    size = State()
    price = State()
    comment_photo = State()
    confirm = State()
    edit_field = State()
    answer_preview = State()


class AdminStates(StatesGroup):
    waiting_excel_upload = State()
    waiting_push_ids = State()
    waiting_push_text = State()
    waiting_push_confirm = State()
    waiting_order_id = State()
    waiting_question_text = State()
    waiting_add_admin_id = State()
    waiting_remove_admin_id = State()
    waiting_macro_title = State()
    waiting_macro_body = State()
    waiting_macro_confirm = State()
    waiting_kind_keyword_add = State()
    waiting_kind_keyword_remove = State()
    waiting_block_user_id = State()
    waiting_unblock_user_id = State()
