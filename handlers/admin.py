"""
handlers/admin.py — Панель администратора v6.

Доступно только ADMIN_ID из .env.
Единственная функция: генерация временных токенов доступа для клиентов.
"""

import logging
import os

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    is_admin, create_token, get_active_tokens,
    get_all_tokens, revoke_token, get_allowed_users_list,
    ADMIN_ID, backup_db, restore_games_from_file, get_total, get_restore_source_path,
)

log = logging.getLogger(__name__)
router = Router()
RESTORE_HISTORY_PATH = os.getenv("RESTORE_HISTORY_PATH", "seed_history.json")


# ── Клавиатуры ────────────────────────────────────────────────────────────────

def kb_admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎟 Токен на 1 день",   callback_data="admin_token_1"),
        InlineKeyboardButton(text="🎟 Токен на 3 дня",   callback_data="admin_token_3"),
    )
    builder.row(
        InlineKeyboardButton(text="🎟 Токен на 7 дней",  callback_data="admin_token_7"),
        InlineKeyboardButton(text="🎟 Токен на 30 дней", callback_data="admin_token_30"),
    )
    builder.row(
        InlineKeyboardButton(text="📋 Активные токены",  callback_data="admin_list_tokens"),
        InlineKeyboardButton(text="👥 Активные юзеры",   callback_data="admin_list_users"),
    )
    builder.row(
        InlineKeyboardButton(text="♻️ Восстановить базу", callback_data="admin_restore_base"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"),
    )
    return builder.as_markup()


def kb_token_actions(token: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="❌ Отозвать этот токен", callback_data=f"admin_revoke_{token}"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ К панели", callback_data="admin_panel"),
    )
    return builder.as_markup()


def kb_back_admin() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="◀️ Назад в панель", callback_data="admin_panel"))
    return builder.as_markup()


# ── Guard: только для администратора ──────────────────────────────────────────

def admin_only(func):
    import functools
    @functools.wraps(func)
    async def wrapper(event, *args, **kwargs):
        uid = (event.from_user.id
               if hasattr(event, "from_user")
               else event.message.from_user.id)
        if not is_admin(uid):
            if isinstance(event, CallbackQuery):
                await event.answer("⛔ Нет доступа.", show_alert=True)
            else:
                await event.answer("⛔ Эта команда доступна только администратору.")
            return
        return await func(event, *args, **kwargs)
    return wrapper


# ── Команда /admin ─────────────────────────────────────────────────────────────

@router.message(Command("admin"))
@admin_only
async def cmd_admin(message: Message):
    await message.answer(
        "🔐 *Панель администратора*\n\n"
        "Здесь вы можете сгенерировать временный токен доступа для клиента.\n"
        "Каждый токен — одноразовый, привязывается к пользователю при активации.\n\n"
        "Ваши данные *никогда* не смешиваются с данными клиентов.\n\n"
        f"_Seed для восстановления: `{RESTORE_HISTORY_PATH}`_",
        parse_mode="Markdown",
        reply_markup=kb_admin_menu(),
    )


@router.callback_query(lambda c: c.data == "admin_panel")
@admin_only
async def cb_admin_panel(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "🔐 *Панель администратора*\n\n"
            "Выберите срок действия токена, просмотрите активных пользователей "
            "или восстановите базу из seed-файла.",
            parse_mode="Markdown",
            reply_markup=kb_admin_menu(),
        )
    except Exception:
        await callback.message.answer(
            "🔐 *Панель администратора*",
            parse_mode="Markdown",
            reply_markup=kb_admin_menu(),
        )
    await callback.answer()


# ── Генерация токенов ──────────────────────────────────────────────────────────

async def _gen_and_show(callback: CallbackQuery, days: int):
    uid = callback.from_user.id
    info = create_token(created_by=uid, days=days)
    token = info["token"]
    exp   = info["expires_at"][:16].replace("T", " ")

    text = (
        f"✅ *Токен создан!*\n\n"
        f"```\n{token}\n```\n\n"
        f"📅 Действует до: `{exp}`\n"
        f"⏱ Срок: {days} {'день' if days == 1 else ('дня' if days < 5 else 'дней')}\n\n"
        f"📤 Отправьте этот код клиенту.\n"
        f"Клиент вводит его командой `/start` — бот попросит токен автоматически."
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=kb_token_actions(token)
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="Markdown",
            reply_markup=kb_token_actions(token)
        )
    await callback.answer(f"Токен на {days} д. создан!")


@router.callback_query(lambda c: c.data == "admin_token_1")
@admin_only
async def cb_token_1(callback: CallbackQuery):
    await _gen_and_show(callback, 1)


@router.callback_query(lambda c: c.data == "admin_token_3")
@admin_only
async def cb_token_3(callback: CallbackQuery):
    await _gen_and_show(callback, 3)


@router.callback_query(lambda c: c.data == "admin_token_7")
@admin_only
async def cb_token_7(callback: CallbackQuery):
    await _gen_and_show(callback, 7)


@router.callback_query(lambda c: c.data == "admin_token_30")
@admin_only
async def cb_token_30(callback: CallbackQuery):
    await _gen_and_show(callback, 30)


# ── Список токенов ─────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "admin_list_tokens")
@admin_only
async def cb_list_tokens(callback: CallbackQuery):
    uid    = callback.from_user.id
    tokens = get_active_tokens(uid)

    if not tokens:
        text = "📋 *Активных токенов нет.*\nСоздайте новый через панель."
    else:
        lines = ["📋 *Активные токены:*\n"]
        for t in tokens:
            exp     = t["expires_at"][:16].replace("T", " ")
            used_by = f"👤 {t['used_by']}" if t["used_by"] else "не активирован"
            lines.append(
                f"`{t['token']}` — до `{exp}`\n"
                f"   {used_by}"
            )
        text = "\n\n".join(lines)

    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=kb_back_admin()
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="Markdown",
            reply_markup=kb_back_admin()
        )
    await callback.answer()


# ── Список пользователей ───────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "admin_list_users")
@admin_only
async def cb_list_users(callback: CallbackQuery):
    users = get_allowed_users_list()
    if not users:
        text = "👥 *Активных клиентов нет.*"
    else:
        lines = [f"👥 *Активных клиентов: {len(users)}*\n"]
        for u in users:
            exp     = u["expires_at"][:10]
            granted = u["granted_at"][:10]
            lines.append(
                f"ID: `{u['user_id']}`\n"
                f"   Активирован: {granted} · До: {exp}"
            )
        text = "\n\n".join(lines)

    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=kb_back_admin()
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="Markdown",
            reply_markup=kb_back_admin()
        )
    await callback.answer()


# ── Отзыв токена ──────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("admin_revoke_"))
@admin_only
async def cb_revoke_token(callback: CallbackQuery):
    token = callback.data.split("admin_revoke_", 1)[1]
    ok = revoke_token(token)
    if ok:
        await callback.answer(f"✅ Токен {token} отозван.", show_alert=True)
    else:
        await callback.answer("⚠️ Токен не найден.", show_alert=True)
    # Вернуть в панель
    try:
        await callback.message.edit_text(
            "🔐 *Панель администратора*",
            parse_mode="Markdown",
            reply_markup=kb_admin_menu(),
        )
    except Exception:
        pass


@router.callback_query(lambda c: c.data == "admin_restore_base")
@admin_only
async def cb_restore_base(callback: CallbackQuery):
    try:
        backup_db()
        restore_source = get_restore_source_path(RESTORE_HISTORY_PATH)
        stats = restore_games_from_file(restore_source, truncate=True)
        total = get_total(callback.from_user.id)
        text = (
            "♻️ *База восстановлена*\n\n"
            f"Источник: `{stats['source']}`\n"
            f"Строк в файле: *{stats['rows']}*\n"
            f"Добавлено: *{stats['inserted']}*\n"
            f"Пропущено как дубли: *{stats['skipped']}*\n"
            f"Игр в вашей базе сейчас: *{total}*"
        )
        try:
            await callback.message.edit_text(
                text,
                parse_mode="Markdown",
                reply_markup=kb_back_admin(),
            )
        except Exception:
            await callback.message.answer(
                text,
                parse_mode="Markdown",
                reply_markup=kb_back_admin(),
            )
        await callback.answer("База восстановлена.", show_alert=True)
    except Exception as exc:
        await callback.answer(f"Ошибка восстановления: {exc}", show_alert=True)
