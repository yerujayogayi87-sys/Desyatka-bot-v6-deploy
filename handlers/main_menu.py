"""
handlers/main_menu.py — /start, главное меню, контроль доступа v6.

Логика доступа:
  - ADMIN_ID  → полный доступ + кнопка "Панель админа"
  - Новый юзер → экран активации токена
  - Токен истёк → повторный запрос
  - Клиент с токеном → обычное меню (данные изолированы от владельца)
"""

import logging
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_total, is_admin, is_allowed, activate_token

log = logging.getLogger(__name__)
router = Router()


class TokenState(StatesGroup):
    waiting_token = State()


# ── Клавиатуры ────────────────────────────────────────────────────────────────

def kb_main_menu(uid: int = 0) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить игру",   callback_data="add_game"),
        InlineKeyboardButton(text="🎯 Прогноз",         callback_data="get_predict"),
    )
    builder.row(
        InlineKeyboardButton(text="⚖️ Чёт / Нечет",     callback_data="predict_even_odd"),
        InlineKeyboardButton(text="🤖 Автопилот",        callback_data="autopilot"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика",       callback_data="stats"),
        InlineKeyboardButton(text="📜 История",          callback_data="history"),
    )
    builder.row(
        InlineKeyboardButton(text="🔥 Горячая полоса",  callback_data="hot_streak"),
        InlineKeyboardButton(text="⚙️ Настройки",        callback_data="settings"),
    )
    if is_admin(uid):
        builder.row(
            InlineKeyboardButton(text="🔐 Панель администратора", callback_data="admin_panel"),
        )
    return builder.as_markup()


def kb_request_token() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔑 Ввести токен", callback_data="enter_token"))
    return builder.as_markup()


# ── Тексты ────────────────────────────────────────────────────────────────────

def _welcome_text(uid: int, total: int) -> str:
    admin_note = "\n🔐 _Вы — администратор_" if is_admin(uid) else ""
    return (
        f"🎰 *Бот «Десятка» v6*{admin_note}\n\n"
        "Анализирую историю и строю прогнозы через:\n"
        "Статистику · EMA · Серии · Марков · Зигзаг · Время\n\n"
        "💡 Быстрый ввод: `3 7 2 5 1` — добавит 5 игр сразу\n\n"
        f"📁 Игр в базе: *{total}*"
    )


def _no_access_text() -> str:
    return (
        "🔒 *Доступ закрыт*\n\n"
        "Этот бот работает только по токену доступа.\n"
        "Запросите токен у администратора и введите его ниже.\n\n"
        "_Ваши данные хранятся отдельно от других пользователей._"
    )


def _token_expired_text() -> str:
    return (
        "⏳ *Срок доступа истёк*\n\n"
        "Ваш токен больше не действителен.\n"
        "Запросите новый у администратора."
    )


# ── /start ────────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    await state.clear()

    if not is_allowed(uid):
        await state.set_state(TokenState.waiting_token)
        await message.answer(
            _no_access_text(),
            parse_mode="Markdown",
            reply_markup=kb_request_token(),
        )
        return

    total = get_total(uid)
    await message.answer(
        _welcome_text(uid, total),
        parse_mode="Markdown",
        reply_markup=kb_main_menu(uid),
    )


# ── Запрос токена ──────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "enter_token")
async def cb_enter_token(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TokenState.waiting_token)
    try:
        await callback.message.edit_text(
            "🔑 *Введите токен доступа*\n\n"
            "Токен выглядит как 8 символов, например: `AB3D7KNP`\n"
            "Буквы и цифры, регистр не важен.",
            parse_mode="Markdown",
        )
    except Exception:
        await callback.message.answer(
            "🔑 *Введите токен доступа:*",
            parse_mode="Markdown",
        )
    await callback.answer()


@router.message(TokenState.waiting_token)
async def got_token(message: Message, state: FSMContext):
    uid   = message.from_user.id
    token = (message.text or "").strip()

    if not token:
        await message.answer("Введите токен текстом.")
        return

    # Rate limiting: не более 5 попыток
    data     = await state.get_data()
    attempts = data.get("token_attempts", 0) + 1
    if attempts > 5:
        await state.clear()
        await message.answer(
            "❌ *Слишком много попыток.* Обратитесь к администратору.",
            parse_mode="Markdown",
        )
        return
    await state.update_data(token_attempts=attempts)

    result = activate_token(uid, token)

    if not result["ok"]:
        await message.answer(
            f"❌ *Ошибка:* {result['reason']}\n\n"
            "Попробуйте ещё раз или обратитесь к администратору.",
            parse_mode="Markdown",
            reply_markup=kb_request_token(),
        )
        return

    await state.clear()
    exp   = result.get("expires_at", "")[:10]
    total = get_total(uid)
    await message.answer(
        f"✅ *Доступ открыт!*\n\n"
        f"Токен действителен до: `{exp}`\n\n"
        + _welcome_text(uid, total),
        parse_mode="Markdown",
        reply_markup=kb_main_menu(uid),
    )


# ── Главное меню (callback) ────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id

    if not is_allowed(uid):
        await state.set_state(TokenState.waiting_token)
        try:
            await callback.message.edit_text(
                _no_access_text(),
                parse_mode="Markdown",
                reply_markup=kb_request_token(),
            )
        except Exception:
            await callback.message.answer(
                _no_access_text(),
                parse_mode="Markdown",
                reply_markup=kb_request_token(),
            )
        await callback.answer()
        return

    total = get_total(uid)
    try:
        await callback.message.edit_text(
            _welcome_text(uid, total),
            parse_mode="Markdown",
            reply_markup=kb_main_menu(uid),
        )
    except Exception:
        await callback.message.answer(
            _welcome_text(uid, total),
            parse_mode="Markdown",
            reply_markup=kb_main_menu(uid),
        )
    await callback.answer()
