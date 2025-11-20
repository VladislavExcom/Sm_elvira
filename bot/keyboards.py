from typing import List, Optional, Tuple

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .context import get_admins

BRAND_SUGGESTIONS = ["Nike", "Adidas", "Jordan", "Puma", "New Balance", "Reebok"]


def main_kb(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(text="➕ Оставить заявку", callback_data="menu:create"),
            InlineKeyboardButton(text="📋 Мои заявки", callback_data="menu:orders"),
        ],
        [InlineKeyboardButton(text="ℹ️ О боте", callback_data="menu:info")],
    ]
    admins = get_admins()
    if user_id in admins:
        buttons.append(
            [
                InlineKeyboardButton(text="📥 Выгрузить заявки", callback_data="menu:admin_reports"),
                InlineKeyboardButton(text="📤 Синхронизировать статусы", callback_data="menu:admin_status"),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(text="❓ Связаться с клиентом", callback_data="menu:admin_question"),
                InlineKeyboardButton(text="📢 Push-рассылка", callback_data="menu:admin_push"),
            ]
        )
        buttons.append(
            [
                InlineKeyboardButton(text="⚙️ Админ-настройки", callback_data="menu:admin_settings"),
                InlineKeyboardButton(text="📊 Аналитика", callback_data="menu:analytics"),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def compact_inline_cancel_back(prev: Optional[str] = None, skip: bool = False) -> InlineKeyboardMarkup:
    row: List[InlineKeyboardButton] = []
    if prev:
        row.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"back:{prev}"))
    if skip:
        row.append(InlineKeyboardButton(text="➡️ Пропустить", callback_data="skip"))
    row.append(InlineKeyboardButton(text="🏠 В меню", callback_data="cancel"))
    return InlineKeyboardMarkup(inline_keyboard=[row])


def brand_prompt_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for brand in BRAND_SUGGESTIONS:
        row.append(InlineKeyboardButton(text=brand, callback_data=f"brand_suggest:{brand}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back:product"),
            InlineKeyboardButton(text="🏠 В меню", callback_data="cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_edit_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Всё верно", callback_data="confirm:yes"),
                InlineKeyboardButton(text="✏️ Изменить", callback_data="confirm:edit"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="confirm:cancel"),
            ]
        ]
    )


def edit_fields_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Товар", callback_data="edit_field:product"),
                InlineKeyboardButton(text="Бренд", callback_data="edit_field:brand"),
            ],
            [
                InlineKeyboardButton(text="Размер", callback_data="edit_field:size"),
                InlineKeyboardButton(text="Цена", callback_data="edit_field:price"),
            ],
            [
                InlineKeyboardButton(text="Комментарий", callback_data="edit_field:comment"),
                InlineKeyboardButton(text="⬅️ К предпросмотру", callback_data="edit_field:back"),
            ],
        ]
    )


def edit_value_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ К предпросмотру", callback_data="edit_preview"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="cancel"),
            ]
        ]
    )


def order_actions_user_inline(order_id: int, allow_actions: bool) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if allow_actions:
        rows.append(
            [
                InlineKeyboardButton(text="✏ Изменить заявку", callback_data=f"user_edit:{order_id}"),
                InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"user_delete:{order_id}"),
            ]
        )

    rows.append([InlineKeyboardButton(text="⬅️ К списку заявок", callback_data="menu:orders")])
    rows.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="user_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def orders_list_inline(order_ids: List[int]) -> Optional[InlineKeyboardMarkup]:
    if not order_ids:
        return None
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for oid in order_ids:
        row.append(InlineKeyboardButton(text=str(oid), callback_data=f"show_order:{oid}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🏠 В меню", callback_data="user_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_settings_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Администраторы", callback_data="settings:admins"),
            ],
            [
                InlineKeyboardButton(text="🧠 Макросы", callback_data="settings:macros"),
            ],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="settings:home")],
        ]
    )


def admin_admins_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Добавить", callback_data="settings:add_admin"),
                InlineKeyboardButton(text="➖ Удалить", callback_data="settings:remove_admin"),
            ],
            [
                InlineKeyboardButton(text="↩️ Назад", callback_data="settings:back"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="settings:home"),
            ],
        ]
    )


def admin_id_prompt_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К списку", callback_data="settings:admins")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="settings:home")],
        ]
    )


def macros_list_inline(items: List[Tuple[int, str]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if items:
        for macro_id, title in items:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"#{macro_id} · {title[:20]}",
                        callback_data=f"macro:open:{macro_id}",
                    )
                ]
            )
    else:
        rows.append(
            [
                InlineKeyboardButton(text="Макросов нет", callback_data="macro:create"),
            ]
        )
    rows.append([InlineKeyboardButton(text="➕ Создать макрос", callback_data="macro:create")])
    rows.append(
        [
            InlineKeyboardButton(text="↩️ Назад", callback_data="macro:back"),
            InlineKeyboardButton(text="🏠 В меню", callback_data="macro:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def macro_input_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ К списку", callback_data="macro_input:list")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="macro_input:home")],
        ]
    )


def macro_detail_inline(macro_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Править", callback_data=f"macro:edit:{macro_id}"),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"macro:delete:{macro_id}"),
            ],
            [
                InlineKeyboardButton(text="↩️ К списку", callback_data="macro:list"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="macro:home"),
            ],
        ]
    )


def macro_confirm_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💾 Сохранить", callback_data="macro_confirm:save")],
            [
                InlineKeyboardButton(text="✏️ Заголовок", callback_data="macro_confirm:title"),
                InlineKeyboardButton(text="📝 Текст", callback_data="macro_confirm:body"),
            ],
            [
                InlineKeyboardButton(text="↩️ К списку", callback_data="macro_confirm:list"),
                InlineKeyboardButton(text="🏠 В меню", callback_data="macro_confirm:home"),
            ],
        ]
    )


def report_choice_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Полный файл", callback_data="report:full"),
                InlineKeyboardButton(text="🧰 Рабочий файл", callback_data="report:work"),
            ],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="report:back")],
        ]
    )


def cancel_only_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 В меню", callback_data="cancel")]]
    )


def push_preview_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Отправить", callback_data="push_confirm:send"),
                InlineKeyboardButton(text="✏️ Исправить текст", callback_data="push_confirm:edit"),
            ],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="push_confirm:cancel")],
        ]
    )


def admin_question_templates_inline(items: List[Tuple[int, str]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for macro_id, title in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{macro_id} · {title[:24]}",
                    callback_data=f"question_template:{macro_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="✏️ Написать свой вопрос", callback_data="question_template:custom")])
    rows.append(
        [
            InlineKeyboardButton(text="↩️ Ввести другой ID", callback_data="question_template:back"),
            InlineKeyboardButton(text="🏠 В меню", callback_data="question_template:home"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def analytics_inline() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="analytics:refresh")],
            [InlineKeyboardButton(text="🏠 В меню", callback_data="analytics:home")],
        ]
    )
