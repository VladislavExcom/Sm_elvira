from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, declarative_base, mapped_column

from .constants import STATUS_NEW

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    # Telegram chat IDs exceed 32-bit, so we use BigInteger to avoid overflow
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(255))
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_order_number: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default=STATUS_NEW)
    product: Mapped[Optional[str]] = mapped_column(String(512))
    brand: Mapped[Optional[str]] = mapped_column(String(256))
    size: Mapped[Optional[str]] = mapped_column(String(128))
    desired_price: Mapped[Optional[str]] = mapped_column(String(64))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    user_comments: Mapped[Optional[str]] = mapped_column(Text)
    photos: Mapped[Optional[str]] = mapped_column(Text)
    product_link: Mapped[Optional[str]] = mapped_column(Text)
    communication: Mapped[Optional[str]] = mapped_column(Text)
    internal_comments: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)


class MacroTemplate(Base):
    __tablename__ = "macro_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrderPhoto(Base):
    __tablename__ = "order_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(128))
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OrderStatusLog(Base):
    __tablename__ = "order_status_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class KindKeyword(Base):
    __tablename__ = "kind_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    keyword: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
