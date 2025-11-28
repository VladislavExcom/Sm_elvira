# coding: utf-8
"""
Монолитный main.py — вариант B (структурированный один файл)
Aiogram 3.x + async SQLAlchemy + PostgreSQL + openpyxl
Комментарий: все сообщения и комментарии на русском.
Перед запуском: вставь BOT_TOKEN и DATABASE_DSN в секцию CONFIG.
Установи зависимости:
pip install aiogram sqlalchemy asyncpg pandas openpyxl
"""

import os
import asyncio
import logging
import secrets
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy import func, select, text

from .config import load_settings, Settings
from .constants import (
    STATUS_ADDED,
    STATUS_ANSWER_RECEIVED,
    STATUS_CLARIFY,
    STATUS_DELETED_BY_USER,
    STATUS_IN_QUEUE,
    STATUS_LIST,
    STATUS_NEW,
    STATUS_NOT_ADDED,
)
from .context import (
    get_admins,
    get_bot,
    get_database,
    get_session_factory,
    get_settings,
    init_context,
    refresh_admins_cache,
)
from .db import Database
from .logging_config import setup_logging
from .metrics import setup_metrics_server
from .middlewares import MetricsMiddleware
from .keyboards import (
    admin_admins_inline,
    admin_id_prompt_inline,
    admin_question_templates_inline,
    admin_settings_inline,
    analytics_inline,
    brand_prompt_keyboard,
    cancel_only_inline,
    compact_inline_cancel_back,
    confirm_edit_inline,
    edit_fields_inline,
    edit_value_inline,
    macro_confirm_inline,
    macro_detail_inline,
    macro_input_inline,
    macros_list_inline,
    main_kb,
    order_actions_user_inline,
    orders_list_inline,
    report_choice_inline,
    push_preview_inline,
    kind_list_inline,
    kind_detail_inline,
)
from .utils.photos import pack_photo_entry, parse_photo_entries, telegram_file_url
from .models import AdminAction, Base, KindKeyword, MacroTemplate, Order, OrderStatusLog, User
from .services.files import safe_remove_file
from .services.reports import generate_order_reports, prepare_status_updates
from .services.photos import persist_order_photos, restore_order_photos
# from .states...
from .states import AdminStates, OrderStates
from .models import KindKeyword

# ---------------- CONFIG / LOGGING ----------------
setup_logging()
settings: Settings = load_settings()
PHOTOS_DIR = str(settings.photos_dir)
TMP_DIR = str(settings.tmp_dir)

FINAL_ORDER_STATUSES = {STATUS_ADDED, STATUS_NOT_ADDED, STATUS_DELETED_BY_USER}
STATUS_DESCRIPTIONS = {
    STATUS_NEW: "Мы только получили заявку и уже начали поиск.",
    STATUS_IN_QUEUE: "Заявка в работе — команда мониторит наличие.",
    STATUS_CLARIFY: "Нужна дополнительная информация, мы уточняем детали.",
    STATUS_ANSWER_RECEIVED: "Спасибо за ответ! Передали его закупщикам.",
}
STATUS_SHORT = {
    STATUS_NEW: "В работе",
    STATUS_IN_QUEUE: "В работе",
    STATUS_CLARIFY: "В работе",
    STATUS_ANSWER_RECEIVED: "В работе",
    STATUS_ADDED: "Добавлен",
    STATUS_NOT_ADDED: "Не будет добавлен",
    STATUS_DELETED_BY_USER: "Удалена пользователем",
}
KIND_VALUES = ["Одежда", "Обувь", "Инвентарь", "Аксессуары"]

ADMIN_QUESTION_TEMPLATES = [
    (
        "Уточнить размер",
        "Подскажите, пожалуйста, какой размер вам подойдёт? Это поможет точнее найти товар.",
    ),
    (
        "Уточнить бюджет",
        "Подтвердите, какой бюджет комфортен за этот товар, чтобы мы искали в нужном диапазоне.",
    ),
    (
        "Предложить альтернативу",
        "Нашлась похожая модель. Готовы рассмотреть альтернативу, если она появится быстрее?",
    ),
]

logger = logging.getLogger(__name__)

# ---------------- BOT / DISPATCHER / FSM / DB ----------------
database = Database(settings)
bot = Bot(token=settings.bot_token)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.update.middleware(MetricsMiddleware())

init_context(bot, database.session_factory, settings, database)

# ---------------- DB HELPERS ----------------
async def init_db():
    db = get_database()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        try:
            await conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS public_id VARCHAR(32) UNIQUE"))
            await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS user_order_number INTEGER"))
            await conn.execute(text("""
            CREATE OR REPLACE VIEW view_orders_count_status AS
            SELECT status, COUNT(*) AS cnt FROM orders GROUP BY status;
            """))
            # аналитические представления
            await conn.execute(text("""
            CREATE OR REPLACE VIEW v_order_status_durations AS
            WITH ordered AS (
                SELECT
                    order_id,
                    status,
                    ts,
                    LEAD(ts) OVER (PARTITION BY order_id ORDER BY ts) AS next_ts
                FROM order_status_logs
            ),
            durations AS (
                SELECT
                    order_id,
                    status,
                    ts,
                    next_ts,
                    EXTRACT(EPOCH FROM (COALESCE(next_ts, NOW()) - ts)) AS seconds_in_status
                FROM ordered
            )
            SELECT * FROM durations;
            """))
            await conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_status_avg"))
            await conn.execute(text("""
            CREATE MATERIALIZED VIEW mv_status_avg AS
            SELECT
                status,
                AVG(seconds_in_status) AS avg_seconds,
                SUM(seconds_in_status) AS total_seconds,
                COUNT(*) AS transitions,
                NOW() AS refreshed_at
            FROM v_order_status_durations
            WHERE next_ts IS NOT NULL
            GROUP BY status;
            """))
            await conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_order_status_time"))
            await conn.execute(text("""
            CREATE MATERIALIZED VIEW mv_order_status_time AS
            SELECT
                order_id,
                status,
                SUM(seconds_in_status) AS seconds_in_status
            FROM v_order_status_durations
            WHERE next_ts IS NOT NULL
            GROUP BY order_id, status;
            """))
            await conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_order_stats"))
            await conn.execute(text(f"""
            CREATE MATERIALIZED VIEW mv_order_stats AS
            SELECT
                (SELECT COUNT(*) FROM orders) AS total_orders,
                (SELECT COUNT(*) FROM orders WHERE status IN ('{STATUS_ADDED}','{STATUS_NOT_ADDED}','{STATUS_DELETED_BY_USER}')) AS closed_orders,
                (SELECT COUNT(*) FROM orders WHERE status NOT IN ('{STATUS_ADDED}','{STATUS_NOT_ADDED}','{STATUS_DELETED_BY_USER}')) AS active_orders,
                (SELECT COUNT(*) FROM orders WHERE status = '{STATUS_ADDED}') AS added_orders,
                CASE WHEN (SELECT COUNT(*) FROM orders) > 0
                     THEN (SELECT COUNT(*) FROM orders WHERE status = '{STATUS_ADDED}')::DECIMAL / (SELECT COUNT(*) FROM orders)
                     ELSE 0 END AS conversion_added,
                (SELECT COUNT(*) FROM users WHERE COALESCE(is_admin, FALSE) = FALSE) AS users_non_admin,
                NOW() AS refreshed_at;
            """))
            await conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_top_brands"))
            await conn.execute(text("""
            CREATE MATERIALIZED VIEW mv_top_brands AS
            SELECT
                COALESCE(NULLIF(TRIM(brand), ''), '—') AS brand,
                COUNT(*) AS cnt,
                NOW() AS refreshed_at
            FROM orders
            GROUP BY 1;
            """))
            await conn.execute(text("DROP MATERIALIZED VIEW IF EXISTS mv_kind_distribution"))
            await conn.execute(text("""
            CREATE MATERIALIZED VIEW mv_kind_distribution AS
            SELECT
                COALESCE(
                    (
                        SELECT kk.kind
                        FROM kind_keywords kk
                        WHERE lower(COALESCE(o.product, '')) LIKE '%' || kk.keyword || '%'
                        LIMIT 1
                    ),
                    'Не определено'
                ) AS kind,
                COUNT(*) AS cnt,
                NOW() AS refreshed_at
            FROM orders o
            GROUP BY 1;
            """))
        except Exception:
            pass

async def refresh_materialized_views():
    db = get_database()
    async with db.engine.begin() as conn:
        for view in [
            "mv_status_avg",
            "mv_order_status_time",
            "mv_order_stats",
            "mv_top_brands",
            "mv_kind_distribution",
        ]:
            try:
                await conn.execute(text(f"REFRESH MATERIALIZED VIEW {view};"))
            except Exception:
                logger.exception("Failed to refresh materialized view %s", view)


async def refresh_views_periodically(interval_hours: int = 4):
    while True:
        try:
            await refresh_materialized_views()
        except Exception:
            logger.exception("Periodic refresh failed")
        await asyncio.sleep(interval_hours * 3600)


async def generate_unique_user_public_id(session) -> str:
    while True:
        candidate = f"{secrets.randbelow(900000) + 100000:06d}"
        exists = await session.scalar(select(User.id).where(User.public_id == candidate))
        if not exists:
            return candidate


async def get_kind_keywords() -> Dict[str, List[str]]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(KindKeyword.kind, KindKeyword.keyword).order_by(KindKeyword.kind))
        kinds: Dict[str, List[str]] = defaultdict(list)
        for kind, kw in q.all():
            kinds[kind].append(kw)
        return kinds


async def add_kind_keyword(kind: str, keyword: str) -> Tuple[bool, str]:
    kind = kind.strip()
    keyword = keyword.strip().lower()
    if not keyword or kind not in KIND_VALUES:
        return False, "Некорректные данные."
    session_factory = get_session_factory()
    async with session_factory() as session:
        existing = await session.execute(select(KindKeyword).where(KindKeyword.keyword == keyword))
        if existing.scalars().first():
            return False, "Слово уже используется в другом виде."
        session.add(KindKeyword(kind=kind, keyword=keyword))
        await session.commit()
    return True, "Добавлено."


async def remove_kind_keyword(kind: str, keyword: str) -> Tuple[bool, str]:
    keyword = keyword.strip().lower()
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(KindKeyword).where(KindKeyword.kind == kind, KindKeyword.keyword == keyword))
        row = q.scalars().first()
        if not row:
            return False, "Такое слово не найдено в этом виде."
        await session.delete(row)
        await session.commit()
    return True, "Удалено."


async def ensure_user_public_id(session, user: User) -> str:
    if not user.public_id:
        user.public_id = await generate_unique_user_public_id(session)
        await session.commit()
    return user.public_id


async def get_user_public_id(user_id: int) -> Optional[str]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(User).where(User.id == user_id))
        user = q.scalars().first()
        if not user:
            return None
        return await ensure_user_public_id(session, user)


def format_order_number(order: Order, user_public_id: Optional[str]) -> str:
    prefix = user_public_id or str(order.user_id)
    suffix = order.user_order_number or order.id
    return f"{prefix}-{suffix}"


async def get_order_display_number(order: Order) -> str:
    """Возвращает номер заказа в формате {public_id}-{user_order_number}."""
    return format_order_number(order, await get_user_public_id(order.user_id))


async def ensure_order_numbers(session, orders: List[Order], user_id: int) -> None:
    missing = [o for o in orders if o.user_order_number is None]
    if not missing:
        return
    max_num = await session.scalar(select(func.max(Order.user_order_number)).where(Order.user_id == user_id))
    next_num = max_num or 0
    for order in sorted(missing, key=lambda o: (o.created_at or datetime.min, o.id)):
        next_num += 1
        order.user_order_number = next_num
    await session.commit()


async def log_status_change(session, order_id: int, status: str, ts: Optional[datetime] = None) -> None:
    ts = ts or datetime.utcnow()
    session.add(OrderStatusLog(order_id=order_id, status=status, ts=ts))

async def add_or_update_user(user_obj):
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(User).where(User.id == user_obj.id))
        u = q.scalars().first()
        if not u:
            public_id = await generate_unique_user_public_id(session)
            u = User(id=user_obj.id, username=getattr(user_obj, "username", None),
                     full_name=getattr(user_obj, "full_name", None), is_admin=(user_obj.id in get_admins()),
                     public_id=public_id)
            session.add(u)
            await session.commit()
        else:
            changed = False
            if u.username != getattr(user_obj, "username", None):
                u.username = getattr(user_obj, "username", None); changed = True
            if u.full_name != getattr(user_obj, "full_name", None):
                u.full_name = getattr(user_obj, "full_name", None); changed = True
            if not u.public_id:
                u.public_id = await generate_unique_user_public_id(session); changed = True
            if changed:
                await session.commit()


class UserSyncMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_obj = getattr(event, "from_user", None)
        if user_obj:
            try:
                await add_or_update_user(user_obj)
            except Exception:
                logger.exception("Failed to sync user meta")
        return await handler(event, data)


dp.update.middleware(UserSyncMiddleware())

async def create_order_db(data: dict, user_id: int) -> Tuple[int, str]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(User).where(User.id == user_id))
        user = q.scalars().first()
        if not user:
            public_id = await generate_unique_user_public_id(session)
            user = User(id=user_id, username=None, full_name=None, is_admin=(user_id in get_admins()), public_id=public_id)
            session.add(user)
        else:
            await ensure_user_public_id(session, user)
        max_number = await session.scalar(select(func.max(Order.user_order_number)).where(Order.user_id == user_id))
        next_number = (max_number or 0) + 1
        order = Order(
            user_id=user_id,
            status=STATUS_NEW,
            product=data.get("product"),
            brand=data.get("brand"),
            size=data.get("size"),
            desired_price=data.get("price"),
            comment=data.get("comment"),
            user_comments="",
            photos=data.get("photos", ""),
            product_link="",
            communication=f"{datetime.utcnow().isoformat()} CREATED by {user_id}",
            internal_comments="",
            user_order_number=next_number
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        await log_status_change(session, order.id, STATUS_NEW, ts=order.created_at)
        await session.commit()
        return order.id, format_order_number(order, user.public_id)

async def get_orders_by_user(user_id: int):
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc()))
        orders = q.scalars().all()
        await ensure_order_numbers(session, orders, user_id)
        return orders

async def get_order_by_id(order_id: int):
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(Order).where(Order.id == order_id))
        order = q.scalars().first()
        if not order:
            return None
        await ensure_order_numbers(session, [order], order.user_id)
        return order

async def update_order_status_db(order_id: int, new_status: str, product_link: str = "") -> bool:
    if new_status == STATUS_ADDED:
        if not (isinstance(product_link, str) and product_link.strip().lower().startswith(("http://", "https://"))):
            return False
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(Order).where(Order.id == order_id))
        order = q.scalars().first()
        if not order:
            return False
        old = order.status
        order.status = new_status
        if product_link:
            order.product_link = product_link
        order.communication = (order.communication or "") + f"\n{datetime.utcnow().isoformat()} ADMIN_STATUS_CHANGE {old} -> {new_status}"
        order.updated_at = datetime.utcnow()
        await log_status_change(session, order.id, new_status, ts=order.updated_at)
        await session.commit()
        return True

async def update_order_details_db(order_id: int, data: dict, actor_id: Optional[int] = None) -> bool:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(Order).where(Order.id == order_id))
        order = q.scalars().first()
        if not order:
            return False
        order.product = data.get("product")
        order.brand = data.get("brand")
        order.size = data.get("size")
        order.desired_price = data.get("price")
        order.comment = data.get("comment")
        order.photos = data.get("photos")
        order.updated_at = datetime.utcnow()
        order.communication = ((order.communication or "") + f"\n{datetime.utcnow().isoformat()} USER_EDIT {actor_id or ''}")
        await session.commit()
        return True


async def append_user_comment_db(order_id: int, user_id: int, text: str) -> bool:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(Order).where(Order.id == order_id))
        order = q.scalars().first()
        if not order:
            return False
        order.user_comments = (order.user_comments or "") + f"\n{datetime.utcnow().isoformat()} USER({user_id}): {text}"
        order.communication = (order.communication or "") + f"\n{datetime.utcnow().isoformat()} USER_COMMENT: {text}"
        order.updated_at = datetime.utcnow()
        await session.commit()
        return True


async def collect_user_answer_text(message: Message) -> str:
    tg_bot = get_bot()
    if message.photo:
        file_info = await tg_bot.get_file(message.photo[-1].file_id)
        local = os.path.join(PHOTOS_DIR, f"{message.from_user.id}_{message.photo[-1].file_unique_id}.jpg")
        await tg_bot.download(file_info, local)
        return f"Фото ответа: {local}\n{message.caption or ''}"
    if message.document:
        doc = message.document
        name_lower = (doc.file_name or "").lower()
        is_image = (doc.mime_type and doc.mime_type.startswith("image/")) or name_lower.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
        )
        if is_image:
            file_info = await tg_bot.get_file(doc.file_id)
            ext = os.path.splitext(doc.file_name or "")[1] or ".jpg"
            local = os.path.join(PHOTOS_DIR, f"{message.from_user.id}_{doc.file_unique_id}{ext}")
            await tg_bot.download(file_info, local)
            return f"Фото ответа: {local}\n{message.caption or message.text or ''}"
    return message.text or ""

async def mark_deleted_by_user_db(order_id: int, user_id: int) -> bool:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(Order).where(Order.id == order_id, Order.user_id == user_id))
        order = q.scalars().first()
        if not order:
            return False
        order.status = STATUS_DELETED_BY_USER
        order.internal_comments = (order.internal_comments or "") + f"\n{datetime.utcnow().isoformat()} Deleted by user {user_id}"
        order.updated_at = datetime.utcnow()
        await log_status_change(session, order.id, STATUS_DELETED_BY_USER, ts=order.updated_at)
        await session.commit()
        return True


async def get_macro_templates() -> List[MacroTemplate]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(MacroTemplate).order_by(MacroTemplate.id))
        return q.scalars().all()


async def get_macro_by_id(macro_id: int) -> Optional[MacroTemplate]:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(MacroTemplate).where(MacroTemplate.id == macro_id))
        return q.scalars().first()


async def create_macro_db(title: str, body: str, admin_id: int) -> int:
    session_factory = get_session_factory()
    async with session_factory() as session:
        macro = MacroTemplate(title=title, body=body, created_by=admin_id, updated_by=admin_id)
        session.add(macro)
        action = AdminAction(admin_id=admin_id, action_type="macro_create", details=f"{title}")
        session.add(action)
        await session.commit()
        await session.refresh(macro)
        return macro.id


async def update_macro_db(macro_id: int, title: str, body: str, admin_id: int) -> bool:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(MacroTemplate).where(MacroTemplate.id == macro_id))
        macro = q.scalars().first()
        if not macro:
            return False
        macro.title = title
        macro.body = body
        macro.updated_by = admin_id
        action = AdminAction(admin_id=admin_id, action_type="macro_edit", details=f"{macro_id}")
        session.add(action)
        await session.commit()
        return True


async def delete_macro_db(macro_id: int, admin_id: int) -> bool:
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(MacroTemplate).where(MacroTemplate.id == macro_id))
        macro = q.scalars().first()
        if not macro:
            return False
        await session.delete(macro)
        action = AdminAction(admin_id=admin_id, action_type="macro_delete", details=f"{macro_id}")
        session.add(action)
        await session.commit()
        return True

# ---------------- UTILITIES ----------------
async def delete_message_later(chat_id: int, message_id: int, delay: int = 5):
    await asyncio.sleep(delay)
    tg_bot = get_bot()
    try:
        await tg_bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


def validate_config() -> None:
    """Проверяет минимальную конфигурацию и логирует предупреждения."""
    cfg = settings
    if not cfg.bot_token:
        logger.error("BOT_TOKEN is not set. Set BOT_TOKEN env var before running the bot.")
        raise RuntimeError("BOT_TOKEN is not configured")
    if not cfg.database_dsn:
        logger.warning("DATABASE_DSN is empty. Database connections will fail without a proper DSN.")

def build_preview_text(data: dict) -> str:
    p = data.get("product", "—")
    b = data.get("brand", "—")
    s = data.get("size", "—")
    c = data.get("comment", "—")
    return (
        "Предпросмотр заявки:\n\n"
        f"Товар: {p}\n"
        f"Бренд: {b}\n"
        f"Размер: {s}\n"
        f"Комментарий: {c}"
    )


INFO_TEXT = (
    "Оставьте заявку на товар, которого пока нет на сайте.\n\n"
    "1️⃣ Нажмите «Оставить заявку» и опишите модель, бренд и размер.\n"
    "2️⃣ Если есть пожелания по цене или фото — приложите их.\n"
    "3️⃣ В разделе «Мои заявки» следите за статусами и отвечайте на уточнения.\n\n"
    "Нажимайте кнопку «🏠 Главное меню», чтобы вернуться на главный экран."
)


async def delete_callback_message(message: Optional[Message]) -> None:
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        pass


async def safe_answer_callback(cb: CallbackQuery, **kwargs) -> None:
    try:
        await cb.answer(**kwargs)
    except TelegramBadRequest:
        logger.debug("Callback answer failed (possibly too old): %s", kwargs)


async def send_main_menu(user_id: int, text: Optional[str] = None) -> None:
    tg_bot = get_bot()
    await tg_bot.send_message(
        chat_id=user_id,
        text=text or "🏠 Главное меню. Что хотите сделать?",
        reply_markup=main_kb(user_id),
    )


async def send_info_message(user_id: int) -> None:
    tg_bot = get_bot()
    await tg_bot.send_message(
        chat_id=user_id,
        text=INFO_TEXT,
        reply_markup=main_kb(user_id),
    )


def answer_review_inline(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить ответ", callback_data=f"answer_confirm:{order_id}"),
                InlineKeyboardButton(text="✏️ Исправить", callback_data=f"answer_edit:{order_id}"),
            ]
        ]
    )


async def send_answer_preview(user_id: int, order_id: int, text: str, state: FSMContext) -> None:
    tg_bot = get_bot()
    data = await state.get_data()
    prev_id = data.get("answer_preview_msg_id")
    if prev_id:
        try:
            await tg_bot.delete_message(chat_id=user_id, message_id=prev_id)
        except Exception:
            pass
    sent = await tg_bot.send_message(
        chat_id=user_id,
        text=(
            f"Ваш ответ:\n\n{text}\n\nОтправить?\n\n"
            "Пришлите новый текст, если нужно поправить — мы обновим превью и кнопки."
        ),
        reply_markup=answer_review_inline(order_id),
    )
    await state.update_data(answer_preview_msg_id=sent.message_id, answer_draft=text, answer_order_id=order_id)


async def start_order_creation_flow(user_id: int, state: FSMContext) -> None:
    await state.clear()
    await state.update_data(last_msg_id=None, prompt_msg_id=None, edit_order_id=None)
    await prompt_stage(user_id, state, "product")


async def send_user_orders_list(user_id: int) -> None:
    tg_bot = get_bot()
    public_id = await get_user_public_id(user_id)
    recs = await get_orders_by_user(user_id)
    if not recs:
        await tg_bot.send_message(
            chat_id=user_id,
            text="Пока нет заявок. Нажмите «Оставить заявку», чтобы описать нужный товар.",
            reply_markup=main_kb(user_id),
        )
        return
    interactive_ids: List[Tuple[int, str]] = []
    bucket_added: List[str] = []
    bucket_not_added: List[str] = []
    bucket_in_progress: List[str] = []
    bucket_cancelled: List[str] = []
    for order in recs:
        await restore_order_photos(order.id)
        order_label = str(order.user_order_number or "")
        order_number_full = format_order_number(order, public_id)
        if order.status not in FINAL_ORDER_STATUSES:
            interactive_ids.append((order.id, order_label))
        title = order.product or "Без названия"
        line_parts: List[str] = [f"#{order_number_full}", title]
        if order.brand or order.size:
            line_parts.append(f"{order.brand or '—'} · {order.size or '—'}")
        photos = parse_photo_entries(order.photos, settings)
        if photos:
            line_parts.append(f"📷{len(photos)}")
        line = " · ".join(line_parts)
        line = f"- {line}"

        if order.status == STATUS_ADDED:
            if order.product_link:
                line += f"\n  Ссылка: {order.product_link}"
            bucket_added.append(line)
        elif order.status == STATUS_NOT_ADDED:
            bucket_not_added.append(line)
        elif order.status == STATUS_DELETED_BY_USER:
            bucket_cancelled.append(line)
        else:
            bucket_in_progress.append(line)

    summary_blocks: List[str] = []
    if bucket_added:
        summary_blocks.append("✅ Уже добавили:\n" + "\n\n".join(bucket_added))
    if bucket_not_added:
        summary_blocks.append("🚫 Не сможем добавить:\n" + "\n\n".join(bucket_not_added))
    if bucket_in_progress:
        summary_blocks.append("🔍 В поиске:\n" + "\n\n".join(bucket_in_progress))
    if bucket_cancelled:
        summary_blocks.append("❎ Отменены:\n" + "\n\n".join(bucket_cancelled))
    keyboard = orders_list_inline(interactive_ids)
    footer = (
        "\n\nНажмите на номер заявки ниже, чтобы открыть карточку и внести изменения."
        if keyboard
        else ""
    )
    await tg_bot.send_message(
        chat_id=user_id,
        text="Ваши заявки:\n\n" + "\n\n".join(summary_blocks) + footer,
        reply_markup=keyboard or main_kb(user_id),
    )


async def prompt_report_choice(user_id: int) -> None:
    tg_bot = get_bot()
    await tg_bot.send_message(
        chat_id=user_id,
        text="Выберите файл для выгрузки:",
        reply_markup=report_choice_inline(),
    )


async def prompt_status_upload(user_id: int, state: FSMContext) -> None:
    await state.set_state(AdminStates.waiting_excel_upload)
    tg_bot = get_bot()
    await tg_bot.send_message(
        chat_id=user_id,
        text="Загрузите рабочий .xlsx файл (колонки «ID заказа» и «Статус» обязательны).",
        reply_markup=compact_inline_cancel_back(prev=None, skip=False),
    )


async def build_basic_analytics_text() -> str:
    session_factory = get_session_factory()
    async with session_factory() as session:
        total = await session.scalar(select(func.count(Order.id)))
        horizon = datetime.utcnow() - timedelta(days=7)
        last_week = await session.scalar(
            select(func.count(Order.id)).where(Order.created_at >= horizon)
        )
        active_users = await session.scalar(select(func.count(func.distinct(Order.user_id))))
        status_rows = await session.execute(select(Order.status, func.count(Order.id)).group_by(Order.status))
        brand_rows = await session.execute(
            select(Order.brand, func.count(Order.id))
            .where(Order.brand.isnot(None))
            .group_by(Order.brand)
            .order_by(func.count(Order.id).desc())
            .limit(3)
        )
    status_counts = {row[0]: row[1] for row in status_rows}
    lines = [
        "📊 Базовая аналитика",
        f"Всего заявок: {total or 0}",
        f"За последние 7 дней: {last_week or 0}",
        f"Уникальных пользователей: {active_users or 0}",
        "",
        "По статусам:",
    ]
    for status in STATUS_LIST:
        cnt = status_counts.get(status)
        if cnt:
            lines.append(f"• {status}: {cnt}")
    top_brands = [row[0] for row in brand_rows if row[0]]
    if top_brands:
        lines.append("")
        lines.append("Популярные бренды: " + ", ".join(top_brands))
    return "\n".join(lines)


async def send_analytics_report(user_id: int, prefix: Optional[str] = None) -> None:
    text = await build_basic_analytics_text()
    if prefix:
        text = f"{prefix}\n\n{text}"
    tg_bot = get_bot()
    await tg_bot.send_message(chat_id=user_id, text=text, reply_markup=analytics_inline())


async def start_push_flow(user_id: int, state: FSMContext) -> None:
    await state.set_state(AdminStates.waiting_push_ids)
    await state.update_data(push_preview_msg_id=None)
    tg_bot = get_bot()
    await tg_bot.send_message(
        chat_id=user_id,
        text="Введите ID пользователей через запятую (пример: 123,456):",
        reply_markup=compact_inline_cancel_back(prev=None, skip=False),
    )


async def deliver_admin_question(order_id: int, admin_id: int, text: str) -> bool:
    order = await get_order_by_id(order_id)
    if not order:
        return False
    order_number = await get_order_display_number(order)
    tg_bot = get_bot()
    try:
        await tg_bot.send_message(chat_id=order.user_id, text=f"🔔 Вопрос по заявке #{order_number}:\n\n{text}")
        session_factory = get_session_factory()
        async with session_factory() as session:
            q = await session.execute(select(Order).where(Order.id == order_id))
            ord_obj = q.scalars().first()
            if not ord_obj:
                return False
            ord_obj.communication = (ord_obj.communication or "") + f"\n{datetime.utcnow().isoformat()} ADMIN_QUESTION: {text}"
            ord_obj.status = STATUS_CLARIFY
            action = AdminAction(admin_id=admin_id, action_type="question", details=f"{order_id}")
            session.add(action)
            await log_status_change(session, ord_obj.id, STATUS_CLARIFY, ts=ord_obj.updated_at or datetime.utcnow())
            await session.commit()
        return True
    except Exception:
        logger.exception("Не удалось отправить сообщение пользователю %s", order.user_id)
        return False


async def start_admin_question_flow(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    old_prompt = data.get("question_prompt_msg_id")
    if old_prompt:
        try:
            await get_bot().delete_message(chat_id=user_id, message_id=old_prompt)
        except Exception:
            pass
    await state.set_state(AdminStates.waiting_order_id)
    tg_bot = get_bot()
    sent = await tg_bot.send_message(
        chat_id=user_id,
        text="Введите ID заявки (номер из таблицы), по которой хотите задать вопрос:",
        reply_markup=compact_inline_cancel_back(prev=None, skip=False),
    )
    await state.update_data(question_prompt_msg_id=sent.message_id)


async def show_admin_settings_menu(user_id: int) -> None:
    tg_bot = get_bot()
    await tg_bot.send_message(
        chat_id=user_id,
        text="Админ-настройки:\nВыберите раздел для точечных действий.",
        reply_markup=admin_settings_inline(),
    )


async def show_macros_menu(user_id: int, notice: Optional[str] = None) -> None:
    macros = await get_macro_templates()
    lines: List[str] = []
    if notice:
        lines.append(notice)
        lines.append("")
    if not macros:
        lines.append("Макросы вопросов пока не созданы.")
    else:
        lines.append("Доступные макросы вопросов:")
        for macro in macros:
            lines.append(f"• #{macro.id} · {macro.title}")
    lines.append("")
    lines.append("Выберите макрос для редактирования или создайте новый.")
    await get_bot().send_message(
        chat_id=user_id,
        text="\n".join(lines),
        reply_markup=macros_list_inline([(m.id, m.title) for m in macros]),
    )


async def show_macro_detail(user_id: int, macro: MacroTemplate) -> None:
    text = f"Макрос #{macro.id}\nЗаголовок: {macro.title}\n\nТекст:\n{macro.body}"
    await get_bot().send_message(
        chat_id=user_id,
        text=text,
        reply_markup=macro_detail_inline(macro.id),
    )


async def clear_macro_preview(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    preview_id = data.get("macro_preview_msg_id")
    if preview_id:
        try:
            await get_bot().delete_message(chat_id=user_id, message_id=preview_id)
        except Exception:
            pass
    await state.update_data(macro_preview_msg_id=None)


async def send_macro_preview(user_id: int, state: FSMContext) -> None:
    await clear_macro_preview(user_id, state)
    data = await state.get_data()
    title = data.get("macro_title", "").strip()
    body = data.get("macro_body", "").strip()
    text = f"Предпросмотр макроса:\n\nЗаголовок: {title or '—'}\n\nТекст:\n{body or '—'}"
    sent = await get_bot().send_message(
        chat_id=user_id,
        text=text,
        reply_markup=macro_confirm_inline(),
    )
    await state.update_data(macro_preview_msg_id=sent.message_id)


async def show_admins_overview(user_id: int, notice: Optional[str] = None) -> None:
    admins = sorted(get_admins())
    session_factory = get_session_factory()
    profiles: Dict[int, User] = {}
    if admins:
        async with session_factory() as session:
            q = await session.execute(select(User).where(User.id.in_(admins)))
            profiles = {u.id: u for u in q.scalars().all()}
    lines: List[str] = []
    if notice:
        lines.append(notice)
        lines.append("")
    if not admins:
        lines.append("Пока ни один администратор не назначен.")
    else:
        lines.append("Список администраторов:")
        for admin_id in admins:
            profile = profiles.get(admin_id)
            name = profile.full_name or profile.username or "Без имени"
            lines.append(f"• {name} — {admin_id}")
    lines.append("")
    lines.append("Используйте кнопки ниже, чтобы обновить список.")
    await get_bot().send_message(
        chat_id=user_id,
        text="\n".join(lines),
        reply_markup=admin_admins_inline(),
    )


async def clear_prompt_message(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    prompt_id = data.get("prompt_msg_id")
    if prompt_id:
        try:
            await get_bot().delete_message(chat_id=user_id, message_id=prompt_id)
        except Exception:
            pass
    await state.update_data(prompt_msg_id=None)


async def prompt_stage(user_id: int, state: FSMContext, stage: str) -> None:
    tg_bot = get_bot()
    await clear_prompt_message(user_id, state)
    if stage == "product":
        await state.set_state(OrderStates.product)
        text = "Введите название товара (пример: Nike Air Max). Кнопка «🏠 Главное меню» всегда возвращает на главный экран."
        markup = cancel_only_inline()
    elif stage == "brand":
        await state.set_state(OrderStates.brand)
        text = "Введите бренд (или '-' если не важно). Можно выбрать из подсказок или ввести свой вариант."
        markup = brand_prompt_keyboard()
    elif stage == "size":
        await state.set_state(OrderStates.size)
        text = "Введите размер (или '-' если не важно). Используйте «⬅️ Назад», если хотите изменить бренд."
        markup = compact_inline_cancel_back(prev="brand", skip=False)
    elif stage == "price":
        await state.set_state(OrderStates.price)
        text = "Введите желаемый бюджет (например: 9990)."
        markup = compact_inline_cancel_back(prev="size", skip=False)
    elif stage == "comment":
        await state.set_state(OrderStates.comment_photo)
        text = "Добавьте комментарий или фото (можно несколько сообщений). Нажмите «➡️ Пропустить», если нечего добавить."
        markup = compact_inline_cancel_back(prev="size", skip=True)
    else:
        return
    sent = await tg_bot.send_message(chat_id=user_id, text=text, reply_markup=markup)
    await state.update_data(prompt_msg_id=sent.message_id)

# ---------------- USER FLOW ----------------
@router.message(CommandStart())
async def cmd_start(message: Message):
    await add_or_update_user(message.from_user)
    welcome = (
        f"Привет, {message.from_user.full_name or message.from_user.username}!\n"
        "Здесь можно оставить пожелание по товару, которого нет на сайте. "
        "Выберите действие ниже."
    )
    await send_main_menu(message.from_user.id, welcome)


@router.callback_query(lambda c: c.data == "menu:create")
async def cb_menu_create(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await delete_callback_message(cb.message)
    await start_order_creation_flow(cb.from_user.id, state)


@router.callback_query(lambda c: c.data == "menu:info")
async def cb_menu_info(cb: CallbackQuery):
    await cb.answer()
    await delete_callback_message(cb.message)
    await send_info_message(cb.from_user.id)

@router.callback_query(lambda c: c.data == "menu:home")
async def cb_menu_home(cb: CallbackQuery):
    await cb.answer()
    await delete_callback_message(cb.message)
    await send_main_menu(cb.from_user.id)


@router.callback_query(lambda c: c.data == "menu:orders")
async def cb_menu_orders(cb: CallbackQuery):
    await cb.answer()
    await delete_callback_message(cb.message)
    await send_user_orders_list(cb.from_user.id)

@router.callback_query(lambda c: c.data == "cancel")
async def cb_cancel_create(cb: CallbackQuery, state: FSMContext):
    await cb.answer("Возвращаю в главное меню.")
    await state.clear()
    try:
        await cb.message.delete()
    except Exception:
        pass
    await send_main_menu(cb.from_user.id)


@router.callback_query(lambda c: c.data and c.data.startswith("back:"))
async def cb_back_to_stage(cb: CallbackQuery, state: FSMContext):
    stage = cb.data.split(":", 1)[1]
    await cb.answer()
    await delete_callback_message(cb.message)
    await state.update_data(prompt_msg_id=None)
    if stage in {"product", "brand", "size", "price", "comment"}:
        await prompt_stage(cb.from_user.id, state, stage)

@router.message(OrderStates.product)
async def product_handler(message: Message, state: FSMContext):
    await clear_prompt_message(message.from_user.id, state)
    await state.update_data(product=message.text)
    await prompt_stage(message.from_user.id, state, "brand")

@router.message(OrderStates.brand)
async def brand_handler(message: Message, state: FSMContext):
    await clear_prompt_message(message.from_user.id, state)
    await state.update_data(brand=message.text)
    await prompt_stage(message.from_user.id, state, "size")


@router.callback_query(lambda c: c.data and c.data.startswith("brand_suggest:"))
async def cb_brand_suggest(cb: CallbackQuery, state: FSMContext):
    value = cb.data.split(":", 1)[1]
    await safe_answer_callback(cb)
    await delete_callback_message(cb.message)
    await state.update_data(brand=value)
    await prompt_stage(cb.from_user.id, state, "size")

@router.message(OrderStates.size)
async def size_handler(message: Message, state: FSMContext):
    await clear_prompt_message(message.from_user.id, state)
    await state.update_data(size=message.text)
    await prompt_stage(message.from_user.id, state, "comment")

@router.message(OrderStates.price)
async def price_handler(message: Message, state: FSMContext):
    await clear_prompt_message(message.from_user.id, state)
    await state.update_data(price="")
    await prompt_stage(message.from_user.id, state, "comment")

@router.callback_query(lambda c: c.data == "skip")
async def cb_skip_comment(cb: CallbackQuery, state: FSMContext):
    tg_bot = get_bot()
    await cb.answer()
    await delete_callback_message(cb.message)
    await state.update_data(comment="", photos="")
    await state.update_data(prompt_msg_id=None)
    data = await state.get_data()
    # удаляем предыдущее превью, если есть
    last_msg_id = data.get("last_msg_id")
    if last_msg_id:
        try:
            await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=last_msg_id)
        except Exception:
            pass
    preview_text = build_preview_text(data)
    sent = await tg_bot.send_message(chat_id=cb.from_user.id, text=preview_text, reply_markup=confirm_edit_inline())
    await state.update_data(last_msg_id=sent.message_id)
    await state.set_state(OrderStates.confirm)

@router.message(OrderStates.comment_photo)
async def comment_photo_handler(message: Message, state: FSMContext):
    tg_bot = get_bot()
    await clear_prompt_message(message.from_user.id, state)
    data = await state.get_data()
    existing_photos = [p for p in (data.get("photos") or "").split(";") if p]
    photos = existing_photos[:]
    existing_comment = data.get("comment") or ""
    comment = None
    if message.photo:
        file_info = await tg_bot.get_file(message.photo[-1].file_id)
        local = os.path.join(PHOTOS_DIR, f"{message.from_user.id}_{message.photo[-1].file_unique_id}.jpg")
        await tg_bot.download(file_info, local)
        public_url = telegram_file_url(file_info.file_path, settings)
        photos.append(pack_photo_entry(local, settings, public_url=public_url))
        comment = message.caption or ""
    elif message.document:
        doc = message.document
        name_lower = (doc.file_name or "").lower()
        is_image = (doc.mime_type and doc.mime_type.startswith("image/")) or name_lower.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif")
        )
        if is_image:
            file_info = await tg_bot.get_file(doc.file_id)
            ext = os.path.splitext(doc.file_name or "")[1] or ".jpg"
            local = os.path.join(PHOTOS_DIR, f"{message.from_user.id}_{doc.file_unique_id}{ext}")
            await tg_bot.download(file_info, local)
            public_url = telegram_file_url(file_info.file_path, settings)
            photos.append(pack_photo_entry(local, settings, public_url=public_url))
            comment = message.caption or message.text or ""
    else:
        if message.text and message.text.strip().lower() != "пропустить":
            comment = message.text
    if comment:
        merged_comment = (existing_comment + "\n" + comment).strip() if existing_comment else comment
    else:
        merged_comment = existing_comment
    await state.update_data(comment=merged_comment, photos=";".join(photos))
    last_msg_id = data.get("last_msg_id")
    if last_msg_id:
        try:
            await tg_bot.delete_message(chat_id=message.from_user.id, message_id=last_msg_id)
        except Exception:
            pass
    new_data = await state.get_data()
    preview_text = build_preview_text(new_data)
    sent = await message.answer(preview_text, reply_markup=confirm_edit_inline())
    await state.update_data(last_msg_id=sent.message_id)
    await state.set_state(OrderStates.confirm)

@router.callback_query(lambda c: c.data and c.data.startswith("confirm:"))
async def cb_confirm(cb: CallbackQuery, state: FSMContext):
    tg_bot = get_bot()
    action = cb.data.split(":", 1)[1]
    data = await state.get_data()
    if action == "yes":
        edit_id = data.get("edit_order_id")
        last_msg_id = data.get("last_msg_id")
        if last_msg_id:
            try:
                await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=last_msg_id)
            except Exception:
                pass
        if edit_id:
            updated = await update_order_details_db(edit_id, data, cb.from_user.id)
            if not updated:
                await cb.answer("Не удалось обновить заявку.", show_alert=True)
                return
            await persist_order_photos(edit_id, parse_photo_entries(data.get("photos"), settings))
            await send_user_orders_list(cb.from_user.id)
            await cb.answer("Заявка обновлена.")
        else:
            order_id, public_order_number = await create_order_db(data, cb.from_user.id)
            await persist_order_photos(order_id, parse_photo_entries(data.get("photos"), settings))
            await tg_bot.send_message(
                chat_id=cb.from_user.id,
                text=(
                    f"Заявка №{public_order_number} отправлена.\n"
                    "Мы сообщим, как только найдём товар или появятся уточнения."
                ),
                reply_markup=main_kb(cb.from_user.id),
            )
            await cb.answer("Заявка отправлена.")
        await state.clear()
    elif action == "edit":
        # удаляем превью
        last_msg_id = data.get("last_msg_id")
        if last_msg_id:
            try:
                await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=last_msg_id)
            except Exception:
                pass
        sent = await tg_bot.send_message(chat_id=cb.from_user.id, text="Выберите поле для редактирования:", reply_markup=edit_fields_inline())
        await state.update_data(last_msg_id=sent.message_id)
        await state.set_state(OrderStates.edit_field)
        await cb.answer()
    elif action == "cancel":
        last_msg_id = data.get("last_msg_id")
        if last_msg_id:
            try:
                await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=last_msg_id)
            except Exception:
                pass
        await send_main_menu(cb.from_user.id)
        await state.clear()
        await cb.answer()

@router.callback_query(lambda c: c.data and c.data.startswith("edit_field:"))
async def cb_edit_field(cb: CallbackQuery, state: FSMContext):
    tg_bot = get_bot()
    field = cb.data.split(":", 1)[1]
    data = await state.get_data()
    if field == "back":
        # вернуть preview
        last_msg_id = data.get("last_msg_id")
        if last_msg_id:
            try:
                await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=last_msg_id)
            except Exception:
                pass
        preview_text = build_preview_text(data)
        sent = await tg_bot.send_message(chat_id=cb.from_user.id, text=preview_text, reply_markup=confirm_edit_inline())
        await state.update_data(last_msg_id=sent.message_id)
        await state.set_state(OrderStates.confirm)
        await cb.answer()
        return

    # удалить превью
    last_msg_id = data.get("last_msg_id")
    if last_msg_id:
        try:
            await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=last_msg_id)
        except Exception:
            pass

    current = data.get(field, "")
    prompt = (
        f"Текущее значение для {field}: {current}\n"
        "Отправьте новое значение. Если передумали, используйте кнопки ниже."
    )
    sent = await tg_bot.send_message(
        chat_id=cb.from_user.id, text=prompt, reply_markup=edit_value_inline()
    )
    await state.update_data(edit_field=field, last_msg_id=sent.message_id)
    await state.set_state(OrderStates.edit_field)
    await cb.answer()

@router.message(OrderStates.edit_field)
async def process_edit_field(message: Message, state: FSMContext):
    tg_bot = get_bot()
    data = await state.get_data()
    field = data.get("edit_field")
    if not field:
        await message.answer("Ошибка. Попробуйте снова.", reply_markup=main_kb(message.from_user.id))
        await state.clear()
        return
    val = message.text
    await state.update_data(**{field: val})
    last_msg_id = data.get("last_msg_id")
    if last_msg_id:
        try:
            await tg_bot.delete_message(chat_id=message.from_user.id, message_id=last_msg_id)
        except Exception:
            pass
    newdata = await state.get_data()
    preview_text = build_preview_text(newdata)
    sent = await message.answer(preview_text, reply_markup=confirm_edit_inline())
    await state.update_data(last_msg_id=sent.message_id)
    await state.set_state(OrderStates.confirm)


@router.callback_query(lambda c: c.data == "edit_preview")
async def cb_edit_preview(cb: CallbackQuery, state: FSMContext):
    tg_bot = get_bot()
    data = await state.get_data()
    last_msg_id = data.get("last_msg_id")
    if last_msg_id:
        try:
            await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=last_msg_id)
        except Exception:
            pass
    session_factory = get_session_factory()
    async with session_factory() as session:
        existing = await session.scalar(select(func.count(MacroTemplate.id)))
        if not existing:
            for title, body in ADMIN_QUESTION_TEMPLATES:
                session.add(MacroTemplate(title=title, body=body, created_by=0, updated_by=0))
            await session.commit()
    preview_text = build_preview_text(data)
    sent = await tg_bot.send_message(
        chat_id=cb.from_user.id, text=preview_text, reply_markup=confirm_edit_inline()
    )
    await state.update_data(last_msg_id=sent.message_id)
    await state.set_state(OrderStates.confirm)
    await cb.answer()

@router.callback_query(lambda c: c.data == "user_back")
async def cb_user_back(cb: CallbackQuery):
    await safe_answer_callback(cb)
    await delete_callback_message(cb.message)
    await send_main_menu(cb.from_user.id)


@router.callback_query(lambda c: c.data and c.data.startswith("show_order:"))
async def cb_show_order(cb: CallbackQuery):
    oid = int(cb.data.split(":", 1)[1])
    ord_obj = await get_order_by_id(oid)
    if not ord_obj:
        await cb.answer("Заявка не найдена.", show_alert=True)
        return
    await delete_callback_message(cb.message)
    status_short = {
        STATUS_NEW: "В работе",
        STATUS_IN_QUEUE: "В работе",
        STATUS_CLARIFY: "В работе",
        STATUS_ANSWER_RECEIVED: "В работе",
        STATUS_ADDED: "Добавлен",
        STATUS_NOT_ADDED: "Не будет добавлен",
        STATUS_DELETED_BY_USER: "Удалена пользователем",
    }.get(ord_obj.status, ord_obj.status)
    order_number = await get_order_display_number(ord_obj)
    text = (
        f"📦 Заявка #{order_number}\n"
        f"Товар: {ord_obj.product or '—'}\n"
        f"Бренд: {ord_obj.brand or '—'}\n"
        f"Размер: {ord_obj.size or '—'}\n"
        f"Статус: {status_short}"
    )
    allow_actions = ord_obj.status not in FINAL_ORDER_STATUSES
    # Отправляем новое сообщение (предыдущая карточка оставляется, но при редактировании удаляется)
    await cb.message.answer(text, reply_markup=order_actions_user_inline(ord_obj.id, allow_actions))
    await cb.answer()

@router.callback_query(lambda c: c.data and c.data.startswith("user_delete:"))
async def cb_user_delete(cb: CallbackQuery):
    tg_bot = get_bot()
    oid = int(cb.data.split(":", 1)[1])
    ok = await mark_deleted_by_user_db(oid, cb.from_user.id)
    if ok:
        await cb.answer("Заявка отменена.")
        try:
            await cb.message.delete()
        except Exception:
            pass
        await tg_bot.send_message(
            cb.from_user.id,
            "Заявка отменена. Главное меню ниже.",
            reply_markup=main_kb(cb.from_user.id),
        )
    else:
        await cb.answer("Не удалось отменить заявку.", show_alert=True)

@router.callback_query(lambda c: c.data and c.data.startswith("user_edit:"))
async def cb_user_edit(cb: CallbackQuery, state: FSMContext):
    tg_bot = get_bot()
    oid = int(cb.data.split(":", 1)[1])
    order = await get_order_by_id(oid)
    if not order or order.user_id != cb.from_user.id:
        await cb.answer("Не ваша заявка.", show_alert=True)
        return
    await restore_order_photos(order.id)
    try:
        await cb.message.delete()
    except Exception:
        pass
    # загружаем в стейт
    await state.update_data(product=order.product, brand=order.brand, size=order.size,
                            price=order.desired_price, comment=order.comment, photos=order.photos, last_msg_id=None, edit_order_id=order.id)
    sent = await tg_bot.send_message(
        cb.from_user.id, "Выберите, что поправить в заявке:", reply_markup=edit_fields_inline()
    )
    await state.update_data(last_msg_id=sent.message_id)
    await state.set_state(OrderStates.edit_field)
    await cb.answer()

@router.callback_query(lambda c: c.data and c.data.startswith("report:"))
async def cb_send_report(cb: CallbackQuery):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    action = cb.data.split(":", 1)[1]
    if action == "back":
        await cb.answer()
        await delete_callback_message(cb.message)
        await send_main_menu(cb.from_user.id, "Вернул главное меню.")
        return
    full_path, work_path = await generate_order_reports(TMP_DIR)
    try:
        if action == "full":
            await cb.message.answer_document(
                document=FSInputFile(full_path),
                caption="Полный файл заявок (архив).",
            )
        elif action == "work":
            await cb.message.answer_document(
                document=FSInputFile(work_path),
                caption="Рабочий файл — редактируйте статусы (выпадающий список).",
            )
        else:
            await cb.answer("Неизвестный вариант.", show_alert=True)
            return
        await delete_callback_message(cb.message)
        await send_main_menu(cb.from_user.id, "Файл отправлен. Главное меню ниже.")
        await cb.answer("Файл отправлен.")
    finally:
        safe_remove_file(full_path)
        safe_remove_file(work_path)


@router.callback_query(lambda c: c.data == "menu:admin_reports")
async def cb_menu_admin_reports(cb: CallbackQuery):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    await delete_callback_message(cb.message)
    await prompt_report_choice(cb.from_user.id)


@router.callback_query(lambda c: c.data == "menu:admin_status")
async def cb_menu_admin_status(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    await delete_callback_message(cb.message)
    await prompt_status_upload(cb.from_user.id, state)


@router.callback_query(lambda c: c.data == "menu:admin_push")
async def cb_menu_admin_push(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    await delete_callback_message(cb.message)
    await start_push_flow(cb.from_user.id, state)


@router.callback_query(lambda c: c.data == "menu:admin_question")
async def cb_menu_admin_question(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    await delete_callback_message(cb.message)
    await start_admin_question_flow(cb.from_user.id, state)


@router.callback_query(lambda c: c.data == "menu:admin_settings")
async def cb_menu_admin_settings(cb: CallbackQuery):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    await delete_callback_message(cb.message)
    await show_admin_settings_menu(cb.from_user.id)


async def show_kind_detail(user_id: int, kind: str, notice: Optional[str] = None) -> None:
    keywords = await get_kind_keywords()
    words = ", ".join(sorted(keywords.get(kind, []))) or "—"
    lines = []
    if notice:
        lines.append(notice)
        lines.append("")
    lines.append(f"Вид: {kind}")
    lines.append(f"Слова: {words}")
    text = "\n".join(lines)
    await get_bot().send_message(chat_id=user_id, text=text, reply_markup=kind_detail_inline(kind))


@router.callback_query(lambda c: c.data == "settings:kinds")
async def cb_settings_kinds(cb: CallbackQuery):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    await delete_callback_message(cb.message)
    await get_bot().send_message(
        chat_id=cb.from_user.id,
        text="Выберите вид для управления ключевыми словами:",
        reply_markup=kind_list_inline(KIND_VALUES),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("kind:open:"))
async def cb_kind_open(cb: CallbackQuery):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    kind = cb.data.split(":", 2)[2]
    await cb.answer()
    await delete_callback_message(cb.message)
    await show_kind_detail(cb.from_user.id, kind)


@router.callback_query(lambda c: c.data and c.data.startswith("kind:add:"))
async def cb_kind_add(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    kind = cb.data.split(":", 2)[2]
    await state.set_state(AdminStates.waiting_kind_keyword_add)
    await state.update_data(kind_selected=kind)
    await cb.answer()
    await delete_callback_message(cb.message)
    await get_bot().send_message(
        chat_id=cb.from_user.id,
        text=f"Введите слово для вида «{kind}». Оно не должно повторяться в других видах.",
        reply_markup=compact_inline_cancel_back(prev=None, skip=False),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("kind:remove:"))
async def cb_kind_remove(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    kind = cb.data.split(":", 2)[2]
    await state.set_state(AdminStates.waiting_kind_keyword_remove)
    await state.update_data(kind_selected=kind)
    await cb.answer()
    await delete_callback_message(cb.message)
    await get_bot().send_message(
        chat_id=cb.from_user.id,
        text=f"Введите слово, которое нужно удалить из вида «{kind}».",
        reply_markup=compact_inline_cancel_back(prev=None, skip=False),
    )


@router.message(AdminStates.waiting_kind_keyword_add)
async def process_kind_keyword_add(message: Message, state: FSMContext):
    if message.from_user.id not in get_admins():
        await state.clear()
        return
    data = await state.get_data()
    kind = data.get("kind_selected")
    word = (message.text or "").strip()
    ok, info = await add_kind_keyword(kind, word)
    if not ok:
        await message.answer(info, reply_markup=kind_detail_inline(kind))
        await state.clear()
        return
    await state.clear()
    await show_kind_detail(message.from_user.id, kind, notice="Слово добавлено.")


@router.message(AdminStates.waiting_kind_keyword_remove)
async def process_kind_keyword_remove(message: Message, state: FSMContext):
    if message.from_user.id not in get_admins():
        await state.clear()
        return
    data = await state.get_data()
    kind = data.get("kind_selected")
    word = (message.text or "").strip()
    ok, info = await remove_kind_keyword(kind, word)
    if not ok:
        await message.answer(info, reply_markup=kind_detail_inline(kind))
        await state.clear()
        return
    await state.clear()
    await show_kind_detail(message.from_user.id, kind, notice="Слово удалено.")

@router.callback_query(lambda c: c.data == "menu:analytics")
async def cb_menu_analytics(cb: CallbackQuery):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    await cb.answer()
    await delete_callback_message(cb.message)
    await send_analytics_report(cb.from_user.id)


@router.callback_query(lambda c: c.data and c.data.startswith("analytics:"))
async def cb_analytics_actions(cb: CallbackQuery):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    action = cb.data.split(":", 1)[1]
    if action == "refresh":
        await delete_callback_message(cb.message)
        await send_analytics_report(cb.from_user.id, "Обновлённые данные:")
        await cb.answer()
    else:
        await delete_callback_message(cb.message)
        await send_main_menu(cb.from_user.id, "Возвращаю главное меню.")
        await cb.answer()

@router.message(AdminStates.waiting_excel_upload)
async def admin_handle_excel_upload(message: Message, state: FSMContext):
    tg_bot = get_bot()
    if message.from_user.id not in get_admins():
        return
    if not message.document:
        await message.answer("Загрузите .xlsx файл.")
        return
    if not message.document.file_name.endswith(".xlsx"):
        await message.answer("Файл должен иметь расширение .xlsx")
        return
    tmp_path = os.path.join(TMP_DIR, f"upload_{message.document.file_unique_id}.xlsx")
    file = await tg_bot.get_file(message.document.file_id)
    await tg_bot.download(file, tmp_path)

    errors, updates = await prepare_status_updates(tmp_path)
    if errors:
        await message.answer("Ошибки:\n" + "\n".join(errors))
        safe_remove_file(tmp_path)
        await state.clear()
        return

    session_factory = get_session_factory()
    notify = defaultdict(list)
    updated = 0
    async with session_factory() as session:
        for oid, payload in updates.items():
            q = await session.execute(select(Order).where(Order.id == oid))
            ord_obj = q.scalars().first()
            if not ord_obj:
                continue
            new_status = payload["status"]
            link = payload.get("product_link", "")
            changed = False
            if new_status != ord_obj.status:
                ord_obj.status = new_status
                changed = True
            if link and link != (ord_obj.product_link or ""):
                ord_obj.product_link = link
                changed = True
            if not changed:
                continue
            await ensure_order_numbers(session, [ord_obj], ord_obj.user_id)
            user = await session.get(User, ord_obj.user_id)
            public_id = await ensure_user_public_id(session, user) if user else None
            order_number = format_order_number(ord_obj, public_id)
            ord_obj.communication = (ord_obj.communication or "") + f"\n{datetime.utcnow().isoformat()} ADMIN_UPDATE: {new_status}"
            ord_obj.updated_at = datetime.utcnow()
            updated += 1
            if new_status == STATUS_ADDED:
                notify[ord_obj.user_id].append(f"🎉 Заявка #{order_number}: товар найден. Ссылка: {link or '—'}")
            elif new_status == STATUS_NOT_ADDED:
                notify[ord_obj.user_id].append(f"😔 Заявка #{order_number}: пока не можем добавить товар.")
            elif new_status == STATUS_CLARIFY:
                notify[ord_obj.user_id].append(f"🔍 Заявка #{order_number}: требуется уточнение.")
            elif new_status == STATUS_ANSWER_RECEIVED:
                notify[ord_obj.user_id].append(f"✅ Заявка #{order_number}: получили ваш ответ.")
        await session.commit()

    for uid, msgs in notify.items():
        text = "Обновления по вашим заявкам:\n\n" + "\n".join(msgs)
        try:
            await tg_bot.send_message(chat_id=int(uid), text=text)
        except Exception:
            logger.exception("Не удалось уведомить пользователя %s", uid)

    await message.answer(f"Обновлено: {updated}", reply_markup=main_kb(message.from_user.id))
    safe_remove_file(tmp_path)
    await state.clear()

@router.message(AdminStates.waiting_push_ids)
async def admin_receive_push_ids(message: Message, state: FSMContext):
    try:
        ids = [int(x.strip()) for x in message.text.split(",") if x.strip()]
    except Exception:
        await message.answer("Неверный формат. Введите ID через запятую.")
        return
    await state.update_data(push_ids=ids, push_text="", push_preview_msg_id=None)
    await state.set_state(AdminStates.waiting_push_text)
    await message.answer(f"ID получены: {', '.join(map(str, ids))}. Пришлите текст пуша:", reply_markup=compact_inline_cancel_back(prev=None, skip=False))


@router.message(AdminStates.waiting_push_text)
async def admin_send_push_text(message: Message, state: FSMContext):
    data = await state.get_data()
    ids = data.get("push_ids", [])
    text = message.text or ""
    tg_bot = get_bot()
    preview_id = data.get("push_preview_msg_id")
    if preview_id:
        try:
            await tg_bot.delete_message(chat_id=message.from_user.id, message_id=preview_id)
        except Exception:
            pass
    preview_text = (
        "Предпросмотр рассылки:\n"
        f"Получатели: {', '.join(map(str, ids))}\n\n"
        "Сообщение, которое они получат:\n"
        f"{text}"
    )
    sent = await message.answer(preview_text, reply_markup=push_preview_inline())
    await state.update_data(push_text=text, push_preview_msg_id=sent.message_id)
    await state.set_state(AdminStates.waiting_push_confirm)


@router.callback_query(lambda c: c.data and c.data.startswith("push_confirm:"))
async def cb_push_confirm(cb: CallbackQuery, state: FSMContext):
    tg_bot = get_bot()
    action = cb.data.split(":", 1)[1]
    data = await state.get_data()
    preview_id = data.get("push_preview_msg_id")
    if preview_id:
        try:
            await tg_bot.delete_message(chat_id=cb.from_user.id, message_id=preview_id)
        except Exception:
            pass
    if action == "send":
        ids = data.get("push_ids", [])
        text = data.get("push_text", "")
        success = 0
        fail = 0
        for uid in ids:
            try:
                await tg_bot.send_message(chat_id=int(uid), text=text)
                success += 1
            except Exception:
                logger.exception("Push failed for %s", uid)
                fail += 1
        await send_main_menu(cb.from_user.id, f"Рассылка завершена. Успех: {success}, Ошибок: {fail}")
        await state.clear()
        await safe_answer_callback(cb, text="Отправлено")
    elif action == "edit":
        await state.set_state(AdminStates.waiting_push_text)
        await state.update_data(push_preview_msg_id=None)
        await safe_answer_callback(cb)
        await tg_bot.send_message(
            chat_id=cb.from_user.id,
            text="Введите новый текст рассылки:",
            reply_markup=compact_inline_cancel_back(prev=None, skip=False),
        )
    else:
        await state.clear()
        await send_main_menu(cb.from_user.id, "Рассылка отменена.")
        await safe_answer_callback(cb, text="Отменено")


@router.message(AdminStates.waiting_order_id)
async def admin_receive_order_id(message: Message, state: FSMContext):
    data = await state.get_data()
    prompt_id = data.get("question_prompt_msg_id")
    try:
        oid = int(message.text.strip())
    except Exception:
        await message.answer("ID должен быть числом.")
        return
    order = await get_order_by_id(oid)
    if not order:
        await message.answer("Заявка не найдена. Попробуйте ввести другой номер.")
        return
    order_number = await get_order_display_number(order)
    if prompt_id:
        try:
            await get_bot().delete_message(chat_id=message.from_user.id, message_id=prompt_id)
        except Exception:
            pass
        await state.update_data(question_prompt_msg_id=None)
    await state.update_data(question_order_id=oid)
    await state.set_state(AdminStates.waiting_question_text)
    macros = await get_macro_templates()
    markup = admin_question_templates_inline([(m.id, m.title) for m in macros])
    extra = "" if macros else "\n(Список макросов пуст — введите текст вручную.)"
    await message.answer(
        f"Заявка #{order_number} найдена.\nВыберите типовой вопрос или напишите свой текст.{extra}",
        reply_markup=markup,
    )

@router.message(AdminStates.waiting_question_text)
async def admin_send_question(message: Message, state: FSMContext):
    data = await state.get_data()
    oid = data.get("question_order_id")
    if not oid:
        await message.answer("Ошибка. Начните заново.")
        await state.clear()
        return
    text = message.text or ""
    if not text.strip():
        await message.answer("Введите текст вопроса или выберите шаблон.")
        return
    success = await deliver_admin_question(oid, message.from_user.id, text)
    if success:
        await message.answer("Вопрос отправлен пользователю.", reply_markup=main_kb(message.from_user.id))
    else:
        await message.answer("Не удалось отправить сообщение пользователю.")
    await state.clear()


@router.callback_query(lambda c: c.data and c.data.startswith("question_template:"))
async def cb_question_template(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    action = cb.data.split(":", 1)[1]
    if action == "custom":
        await safe_answer_callback(cb, text="Введите свой вопрос текстом.")
        return
    if action == "back":
        await state.set_state(AdminStates.waiting_order_id)
        await delete_callback_message(cb.message)
        await get_bot().send_message(
            chat_id=cb.from_user.id,
            text="Введите ID заявки, по которой нужен вопрос:",
            reply_markup=compact_inline_cancel_back(prev=None, skip=False),
        )
        await cb.answer()
        return
    if action == "home":
        await state.clear()
        await delete_callback_message(cb.message)
        await send_main_menu(cb.from_user.id, "Возвращаю главное меню.")
        await cb.answer()
        return
    try:
        macro_id = int(action)
    except ValueError:
        await cb.answer("Шаблон не найден.", show_alert=True)
        return
    macro = await get_macro_by_id(macro_id)
    if not macro:
        await cb.answer("Шаблон не найден.", show_alert=True)
        return
    template_text = macro.body
    data = await state.get_data()
    oid = data.get("question_order_id")
    if not oid:
        await cb.answer("ID заявки не найден. Начните заново.", show_alert=True)
        await state.clear()
        return
    await delete_callback_message(cb.message)
    sent = await deliver_admin_question(oid, cb.from_user.id, template_text)
    if sent:
        await state.clear()
        await send_main_menu(cb.from_user.id, "Вопрос отправлен пользователю.")
        await safe_answer_callback(cb, text="Отправлено")
    else:
        await safe_answer_callback(cb, text="Ошибка")
@router.callback_query(lambda c: c.data and c.data.startswith("settings:"))
async def cb_settings(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    tg_bot = get_bot()
    action = cb.data.split(":", 1)[1]
    if action == "admins":
        await delete_callback_message(cb.message)
        await show_admins_overview(cb.from_user.id)
    elif action == "add_admin":
        await delete_callback_message(cb.message)
        await state.set_state(AdminStates.waiting_add_admin_id)
        await tg_bot.send_message(
            chat_id=cb.from_user.id,
            text="Введите ID пользователя для добавления в админы:",
            reply_markup=admin_id_prompt_inline(),
        )
    elif action == "remove_admin":
        await delete_callback_message(cb.message)
        await state.set_state(AdminStates.waiting_remove_admin_id)
        await tg_bot.send_message(
            chat_id=cb.from_user.id,
            text="Введите ID администратора для удаления:",
            reply_markup=admin_id_prompt_inline(),
        )
    elif action == "macros":
        await delete_callback_message(cb.message)
        await show_macros_menu(cb.from_user.id)
    elif action == "back":
        await delete_callback_message(cb.message)
        await show_admin_settings_menu(cb.from_user.id)
    elif action == "home":
        await delete_callback_message(cb.message)
        await send_main_menu(cb.from_user.id, "Возвращаю главное меню.")
    elif action == "stub":
        await safe_answer_callback(cb, text="Эта настройка появится позднее.")
        return
    else:
        await cb.answer("Неизвестное действие.", show_alert=True)
        return
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("macro:"))
async def cb_macro_actions(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    tg_bot = get_bot()
    if action == "create":
        await state.clear()
        await delete_callback_message(cb.message)
        await state.set_state(AdminStates.waiting_macro_title)
        await state.update_data(
            macro_action="create",
            macro_id=None,
            macro_title="",
            macro_body="",
            macro_preview_msg_id=None,
        )
        await tg_bot.send_message(
            chat_id=cb.from_user.id,
            text="Введите заголовок нового макроса:",
            reply_markup=macro_input_inline(),
        )
    elif action == "edit":
        if len(parts) < 3:
            await cb.answer("Не указан ID макроса.", show_alert=True)
            return
        try:
            macro_id = int(parts[2])
        except ValueError:
            await cb.answer("Некорректный ID макроса.", show_alert=True)
            return
        macro = await get_macro_by_id(macro_id)
        if not macro:
            await safe_answer_callback(cb, text="Макрос не найден.")
            await show_macros_menu(cb.from_user.id)
            return
        await state.clear()
        await delete_callback_message(cb.message)
        await state.set_state(AdminStates.waiting_macro_title)
        await state.update_data(
            macro_action="edit",
            macro_id=macro.id,
            macro_title=macro.title,
            macro_body=macro.body,
            macro_preview_msg_id=None,
        )
        await tg_bot.send_message(
            chat_id=cb.from_user.id,
            text=f"Текущий заголовок: {macro.title}\nОтправьте новый заголовок:",
            reply_markup=macro_input_inline(),
        )
    elif action == "open":
        if len(parts) < 3:
            await cb.answer("Не указан ID макроса.", show_alert=True)
            return
        try:
            macro_id = int(parts[2])
        except ValueError:
            await cb.answer("Некорректный ID макроса.", show_alert=True)
            return
        macro = await get_macro_by_id(macro_id)
        if not macro:
            await safe_answer_callback(cb, text="Макрос не найден.")
            await show_macros_menu(cb.from_user.id)
            return
        await state.clear()
        await delete_callback_message(cb.message)
        await show_macro_detail(cb.from_user.id, macro)
    elif action == "back":
        await state.clear()
        await delete_callback_message(cb.message)
        await show_admin_settings_menu(cb.from_user.id)
    elif action == "list":
        await state.clear()
        await delete_callback_message(cb.message)
        await show_macros_menu(cb.from_user.id)
    elif action == "delete":
        if len(parts) < 3:
            await cb.answer("Не указан ID макроса.", show_alert=True)
            return
        try:
            macro_id = int(parts[2])
        except ValueError:
            await cb.answer("Некорректный ID макроса.", show_alert=True)
            return
        ok = await delete_macro_db(macro_id, cb.from_user.id)
        await delete_callback_message(cb.message)
        if ok:
            await show_macros_menu(cb.from_user.id, notice=f"Макрос #{macro_id} удалён.")
            await safe_answer_callback(cb, text="Удалено")
        else:
            await show_macros_menu(cb.from_user.id, notice="Макрос не найден.")
            await safe_answer_callback(cb, text="Не найден")
        await state.clear()
        return
    elif action == "home":
        await state.clear()
        await delete_callback_message(cb.message)
        await send_main_menu(cb.from_user.id, "Возвращаю главное меню.")
    else:
        await cb.answer("Неизвестное действие.", show_alert=True)
        return
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("macro_input:"))
async def cb_macro_input(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    target = cb.data.split(":", 1)[1]
    await clear_macro_preview(cb.from_user.id, state)
    await delete_callback_message(cb.message)
    if target == "list":
        await state.clear()
        await show_macros_menu(cb.from_user.id)
    else:
        await state.clear()
        await send_main_menu(cb.from_user.id, "Возвращаю главное меню.")
    await cb.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("macro_confirm:"))
async def cb_macro_confirm(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in get_admins():
        await cb.answer("Нет доступа.", show_alert=True)
        return
    action = cb.data.split(":", 1)[1]
    data = await state.get_data()
    tg_bot = get_bot()
    if action == "save":
        title = (data.get("macro_title") or "").strip()
        body = (data.get("macro_body") or "").strip()
        if not title or not body:
            await cb.answer("Заполните заголовок и текст.", show_alert=True)
            return
        await clear_macro_preview(cb.from_user.id, state)
        notice = ""
        if data.get("macro_action") == "edit":
            macro_id = data.get("macro_id")
            ok = await update_macro_db(macro_id, title, body, cb.from_user.id)
            if not ok:
                await cb.answer("Не удалось обновить макрос.", show_alert=True)
                return
            notice = f"Макрос #{macro_id} обновлён."
        else:
            macro_id = await create_macro_db(title, body, cb.from_user.id)
            notice = f"Макрос #{macro_id} сохранён."
        await state.clear()
        await show_macros_menu(cb.from_user.id, notice)
        await cb.answer("Сохранено")
    elif action == "title":
        await clear_macro_preview(cb.from_user.id, state)
        await state.set_state(AdminStates.waiting_macro_title)
        current = data.get("macro_title", "")
        await tg_bot.send_message(
            chat_id=cb.from_user.id,
            text=f"Текущее название: {current or '—'}\nОтправьте новое название:",
            reply_markup=macro_input_inline(),
        )
        await cb.answer()
    elif action == "body":
        await clear_macro_preview(cb.from_user.id, state)
        await state.set_state(AdminStates.waiting_macro_body)
        snippet = data.get("macro_body") or ""
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        prompt = "Введите текст макроса."
        if snippet:
            prompt += f"\nТекущее значение:\n{snippet}"
        await tg_bot.send_message(
            chat_id=cb.from_user.id,
            text=prompt,
            reply_markup=macro_input_inline(),
        )
        await cb.answer()
    elif action == "list":
        await clear_macro_preview(cb.from_user.id, state)
        await state.clear()
        await show_macros_menu(cb.from_user.id)
        await cb.answer()
    elif action == "home":
        await clear_macro_preview(cb.from_user.id, state)
        await state.clear()
        await send_main_menu(cb.from_user.id, "Возвращаю главное меню.")
        await cb.answer()
    else:
        await cb.answer("Неизвестное действие.", show_alert=True)


@router.message(AdminStates.waiting_macro_title)
async def process_macro_title(message: Message, state: FSMContext):
    if message.from_user.id not in get_admins():
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Заголовок не может быть пустым.")
        return
    data = await state.get_data()
    existing_body = data.get("macro_body")
    await state.update_data(macro_title=text)
    await state.set_state(AdminStates.waiting_macro_body)
    prompt = "Введите текст макроса."
    if existing_body and data.get("macro_action") == "edit":
        snippet = existing_body if len(existing_body) <= 300 else existing_body[:300] + "..."
        prompt += f"\nТекущий текст:\n{snippet}"
    await message.answer(prompt, reply_markup=macro_input_inline())


@router.message(AdminStates.waiting_macro_body)
async def process_macro_body(message: Message, state: FSMContext):
    if message.from_user.id not in get_admins():
        await state.clear()
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст макроса не может быть пустым.")
        return
    await state.update_data(macro_body=text)
    await state.set_state(AdminStates.waiting_macro_confirm)
    await send_macro_preview(message.from_user.id, state)

@router.message(AdminStates.waiting_add_admin_id)
async def process_add_admin(message: Message, state: FSMContext):
    try:
        new_id = int(message.text.strip())
    except Exception:
        await message.answer("ID должен быть числом.")
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(User).where(User.id == new_id))
        u = q.scalars().first()
        if u:
            u.is_admin = True
        else:
            u = User(id=new_id, username=None, full_name=None, is_admin=True)
            session.add(u)
        action = AdminAction(admin_id=message.from_user.id, action_type="add_admin", details=f"added {new_id}")
        session.add(action)
        await session.commit()
    await refresh_admins_cache()
    await show_admins_overview(message.from_user.id, notice=f"Пользователь {new_id} добавлен в администраторы.")
    await state.clear()

@router.message(AdminStates.waiting_remove_admin_id)
async def process_remove_admin(message: Message, state: FSMContext):
    try:
        rem_id = int(message.text.strip())
    except Exception:
        await message.answer("ID должен быть числом.")
        return
    if rem_id not in get_admins():
        await show_admins_overview(message.from_user.id, notice="Этот пользователь не является админом.")
        await state.clear()
        return
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(User).where(User.id == rem_id))
        u = q.scalars().first()
        if u:
            u.is_admin = False
        action = AdminAction(admin_id=message.from_user.id, action_type="remove_admin", details=f"removed {rem_id}")
        session.add(action)
        await session.commit()
    await refresh_admins_cache()
    await show_admins_overview(message.from_user.id, notice=f"Пользователь {rem_id} удалён из админов.")
    await state.clear()

# --- Обработка ответов от пользователей (когда статус = Уточнение) ---
@router.message()
async def catch_user_answers(message: Message, state: FSMContext):
    current_state = await state.get_state()
    # В режиме редактирования ответа обновляем превью
    if current_state == "OrderStates:answer_preview":
        data = await state.get_data()
        oid = data.get("answer_order_id")
        if not oid:
            await state.clear()
            await message.answer("Не удалось определить заявку. Начните заново.", reply_markup=main_kb(message.from_user.id))
            return
        txt = await collect_user_answer_text(message)
        await send_answer_preview(message.from_user.id, oid, txt, state)
        return
    # Остальные состояния (создание/редактирование заявки) обрабатываются своими хендлерами
    if current_state:
        return
    # Нет активного состояния — проверяем, ждём ли уточнение
    tg_bot = get_bot()
    recs = await get_orders_by_user(message.from_user.id)
    pending = [r for r in recs if r.status == STATUS_CLARIFY]
    if pending:
        order = pending[0]
        oid = order.id
        txt = await collect_user_answer_text(message)
        await state.set_state(OrderStates.answer_preview)
        await send_answer_preview(message.from_user.id, oid, txt, state)
        return
    await message.answer(
        "Я пока не умею обрабатывать такие сообщения. Пожалуйста, пользуйтесь кнопками ниже 👇",
        reply_markup=main_kb(message.from_user.id),
    )

@router.callback_query(lambda c: c.data and c.data.startswith("answer_confirm:"))
async def cb_answer_confirm(cb: CallbackQuery, state: FSMContext):
    oid = int(cb.data.split(":", 1)[1])
    data = await state.get_data()
    if data.get("answer_order_id") != oid:
        await cb.answer("Нет черновика ответа для этой заявки.", show_alert=True)
        return
    txt = data.get("answer_draft")
    if not txt:
        await cb.answer("Сначала отправьте текст ответа.", show_alert=True)
        return
    order = await get_order_by_id(oid)
    order_number = format_order_number(order, await get_user_public_id(order.user_id)) if order else oid
    await append_user_comment_db(oid, cb.from_user.id, txt)
    await update_order_status_db(oid, STATUS_ANSWER_RECEIVED)
    for a in get_admins():
        try:
            await get_bot().send_message(
                chat_id=a,
                text=f"Ответ от пользователя {cb.from_user.id} по заявке #{order_number}:\n\n{txt}",
            )
        except Exception:
            logger.exception("Не удалось отправить администратору")
    await state.clear()
    preview_id = data.get("answer_preview_msg_id")
    if preview_id:
        try:
            await get_bot().delete_message(chat_id=cb.from_user.id, message_id=preview_id)
        except Exception:
            pass
    await safe_answer_callback(cb)
    await send_main_menu(cb.from_user.id, "Ответ отправлен администратору.")

@router.callback_query(lambda c: c.data and c.data.startswith("answer_edit:"))
async def cb_answer_edit(cb: CallbackQuery, state: FSMContext):
    oid = int(cb.data.split(":", 1)[1])
    data = await state.get_data()
    draft = data.get("answer_draft") or "—"
    preview_id = data.get("answer_preview_msg_id")
    if preview_id:
        try:
            await get_bot().delete_message(chat_id=cb.from_user.id, message_id=preview_id)
        except Exception:
            pass
    await state.update_data(answer_order_id=oid)
    await state.set_state(OrderStates.answer_preview)
    await safe_answer_callback(cb)
    await send_answer_preview(cb.from_user.id, oid, draft, state)

# ---------------- START/STOP ----------------
async def on_startup():
    await init_db()
    await refresh_admins_cache()
    # первичное обновление аналитических представлений и фоновой refresh
    try:
        await refresh_materialized_views()
    except Exception:
        logger.exception("Initial refresh of materialized views failed")
    asyncio.create_task(refresh_views_periodically(4))
    setup_metrics_server()
    session_factory = get_session_factory()
    async with session_factory() as session:
        q = await session.execute(select(Order.id, Order.photos))
        orders = q.all()
    for oid, raw in orders:
        await persist_order_photos(oid, parse_photo_entries(raw, settings))
    logger.info("Бот запущен. Таблицы проверены/созданы.")

async def on_shutdown():
    tg_bot = get_bot()
    await tg_bot.session.close()
    await get_database().dispose()
    logger.info("Бот остановлен.")

# ---------------- RUN ----------------
async def main():
    dp.include_router(router)
    await on_startup()
    try:
        logger.info("Start polling")
        await dp.start_polling(bot)
    finally:
        await on_shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")

