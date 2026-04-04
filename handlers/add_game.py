"""
handlers/add_game.py — добавление игры v6.

Новое:
  - Кнопка «+1» предлагает следующий номер игры с подтверждением Да/Нет
  - Access guard: только разрешённые пользователи
"""

import logging
from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import (
    add_game, add_games_bulk, get_games, get_total, get_weights,
    record_prediction, update_strategy_stats, get_settings, is_allowed,
)
from strategies import predict, bet_recommendation, strategy_top_predictions
from analytics import get_alerts, detect_pause, shannon_entropy
from keyboards import kb_number_input, kb_after_add, kb_pause_detected, kb_main_menu
from utils import ne, fmt_pred, fmt_pred_brief, fmt_entropy

log = logging.getLogger(__name__)
router = Router()


class AddGame(StatesGroup):
    waiting_game_number   = State()
    waiting_result        = State()
    waiting_session       = State()
    confirm_next_game_num = State()


def kb_confirm_next_num(proposed: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text=f"✅ Да, игра {proposed}", callback_data="confirm_game_num_yes"),
        InlineKeyboardButton(text="✏️ Нет, введу сам",        callback_data="confirm_game_num_no"),
    )
    builder.row(
        InlineKeyboardButton(text="— пропустить номер —",     callback_data="confirm_game_num_skip"),
    )
    return builder.as_markup()


def _extract_game_number(raw: str):
    s = raw.strip().lstrip("#").strip()
    try:
        return int(s) if s else None
    except ValueError:
        return None


@router.callback_query(lambda c: c.data == "add_game")
async def cb_add_game(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    games    = get_games(uid, limit=1)
    last_num = _extract_game_number(games[0]["game_number"] if games else "")

    if last_num is not None:
        proposed = str(last_num + 1)
        await state.set_state(AddGame.confirm_next_game_num)
        await state.update_data(proposed_game_num=proposed)
        try:
            await callback.message.edit_text(
                f"➕ *Добавление игры*\n\nСледующая игра: *№{proposed}*?",
                parse_mode="Markdown",
                reply_markup=kb_confirm_next_num(proposed),
            )
        except Exception:
            await callback.message.answer(
                f"➕ Следующая игра: *№{proposed}*?",
                parse_mode="Markdown",
                reply_markup=kb_confirm_next_num(proposed),
            )
    else:
        await state.set_state(AddGame.waiting_game_number)
        try:
            await callback.message.edit_text(
                "➕ *Добавление игры*\n\n"
                "Введи номер игры (например, `1234`) или `.` для пропуска.\n\n"
                "💡 *Быстрый ввод нескольких результатов:* `3 7 2 5 1`",
                parse_mode="Markdown",
            )
        except Exception:
            await callback.message.answer(
                "➕ Введи номер игры или `.` для пропуска:",
                parse_mode="Markdown",
            )
    await callback.answer()


@router.callback_query(lambda c: c.data == "confirm_game_num_yes")
async def cb_confirm_yes(callback: CallbackQuery, state: FSMContext):
    data     = await state.get_data()
    proposed = data.get("proposed_game_num", "")
    await state.update_data(game_number=proposed)
    await state.set_state(AddGame.waiting_result)
    try:
        await callback.message.edit_text(
            f"✅ Номер игры: *{proposed}*\n\n🎲 Какое число выпало?",
            parse_mode="Markdown",
            reply_markup=kb_number_input(),
        )
    except Exception:
        await callback.message.answer(
            f"🎲 Игра {proposed} — выпало?",
            parse_mode="Markdown",
            reply_markup=kb_number_input(),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "confirm_game_num_no")
async def cb_confirm_no(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AddGame.waiting_game_number)
    try:
        await callback.message.edit_text(
            "✏️ Введи номер игры (цифры, напр. `1235`) или `.` для пропуска:",
            parse_mode="Markdown",
        )
    except Exception:
        await callback.message.answer("✏️ Введи номер игры:", parse_mode="Markdown")
    await callback.answer()


@router.callback_query(lambda c: c.data == "confirm_game_num_skip")
async def cb_confirm_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(game_number="")
    await state.set_state(AddGame.waiting_result)
    try:
        await callback.message.edit_text(
            "🎲 Без номера — какое число выпало?",
            parse_mode="Markdown",
            reply_markup=kb_number_input(),
        )
    except Exception:
        await callback.message.answer(
            "🎲 Какое число выпало?",
            parse_mode="Markdown",
            reply_markup=kb_number_input(),
        )
    await callback.answer()


@router.message(AddGame.confirm_next_game_num)
async def got_message_in_confirm_state(message: Message, state: FSMContext):
    """Если пользователь написал числа вместо нажатия кнопки — обрабатываем bulk."""
    text = message.text.strip() if message.text else ""
    bulk = _parse_bulk(text)
    if bulk is not None:
        await state.clear()
        await _handle_bulk(message, bulk)
    else:
        await message.answer(
            "Нажми одну из кнопок или введи несколько чисел через пробел (`3 7 2`).",
            parse_mode="Markdown",
        )


@router.message(AddGame.waiting_game_number)
async def got_game_number(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    bulk = _parse_bulk(text)
    if bulk is not None:
        await state.clear()
        await _handle_bulk(message, bulk)
        return
    await state.update_data(game_number="" if text == "." else text)
    await state.set_state(AddGame.waiting_result)
    await message.answer("🎲 Какое число выпало? Нажми кнопку:",
                         reply_markup=kb_number_input())


@router.message(AddGame.waiting_result)
async def got_result_text(message: Message, state: FSMContext):
    text = message.text.strip() if message.text else ""
    bulk = _parse_bulk(text)
    if bulk is not None:
        await state.clear()
        await _handle_bulk(message, bulk)
    else:
        await message.answer(
            "🎲 Нажми кнопку с числом или введи несколько через пробел (`3 7 2`).",
            parse_mode="Markdown",
            reply_markup=kb_number_input(),
        )


def _parse_bulk(text: str):
    parts = text.split()
    if len(parts) < 2:
        return None
    try:
        nums = [int(p) for p in parts]
        if all(0 <= n <= 10 for n in nums):
            return nums
    except ValueError:
        pass
    return None


async def _handle_bulk(message: Message, results: list):
    uid = message.from_user.id
    update_strategy_stats(uid, results[-1])
    add_games_bulk(uid, results)
    total = get_total(uid)
    games = get_games(uid)

    added_nums = " ".join(ne(r) for r in results)
    confirm = (
        f"✅ *Добавлено {len(results)} игр*\n"
        f"Числа: {added_nums}\n"
        f"Всего в базе: *{total}*\n"
    )

    if len(games) >= 5:
        weights    = get_weights(uid)
        preds      = predict(games, weights, top_n=3)
        _record_all_preds(uid, preds)
        _record_strategy_preds(uid, games)
        bet_rec    = bet_recommendation(preds[0], weights) if preds else ""
        alerts     = get_alerts(games)
        H          = shannon_entropy(games, window=25)
        alert_text = ("\n\n" + "\n".join(alerts)) if alerts else ""

        full_text = (
            confirm
            + "\n🎯 *Прогноз на следующую игру:*\n\n"
            + fmt_pred_brief(preds)
            + "\n\n🔁 *Top-3:*\n"
            + fmt_pred(preds)
            + f"\n\n💡 *Ставка:* {bet_rec}"
            + f"\n🌀 Энтропия: {fmt_entropy(H)}"
            + alert_text
        )
    else:
        remaining = 5 - len(games)
        full_text = (
            confirm
            + f"\n⏳ Для прогноза нужно ещё *{remaining}* игр.\n"
            + "_Напиши несколько чисел через пробел, например: `3 7 2 5 1`_"
        )

    await message.answer(full_text, parse_mode="Markdown", reply_markup=kb_after_add())


@router.callback_query(lambda c: c.data and c.data.startswith("num_"))
async def cb_got_result(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != AddGame.waiting_result:
        await callback.answer("Сначала нажми «Добавить игру».")
        return

    try:
        result = int(callback.data.split("_")[1])
        assert 0 <= result <= 10
    except (ValueError, AssertionError, IndexError):
        await callback.answer("Ошибка! Нажми число от 0 до 10.", show_alert=True)
        return

    uid         = callback.from_user.id
    data        = await state.get_data()
    game_number = data.get("game_number", "")

    update_strategy_stats(uid, result)
    new_id = add_game(uid, result, game_number)
    total  = get_total(uid)
    await state.clear()

    games     = get_games(uid)
    settings  = get_settings(uid)
    threshold = settings.get("pause_threshold_min", 60)
    pause_h   = detect_pause(games, threshold_minutes=threshold) if len(games) >= 2 else None

    if pause_h:
        await state.set_state(AddGame.waiting_session)
        await state.update_data(pause_h=pause_h, result=result, new_id=new_id, total=total,
                                 game_number=game_number)
        label = f"Игра {game_number}" if game_number else f"Запись #{new_id}"
        try:
            await callback.message.edit_text(
                f"✅ *{label} добавлена!* Выпало: *{ne(result)}* · Всего: {total}\n\n"
                f"⏸ *Пауза {pause_h} ч.* — краткосрочные паттерны могут не работать.\n\n"
                "Как продолжить?",
                parse_mode="Markdown",
                reply_markup=kb_pause_detected(),
            )
        except Exception:
            await callback.message.answer(
                f"✅ {label} — {ne(result)}\n⏸ Пауза {pause_h} ч.",
                parse_mode="Markdown",
                reply_markup=kb_pause_detected(),
            )
        await callback.answer()
        return

    await _send_add_result(callback, uid, result, new_id, total, game_number, games,
                            pause_detected=False)


@router.callback_query(lambda c: c.data == "new_session")
async def cb_new_session(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid  = callback.from_user.id
    await state.clear()
    games = get_games(uid)
    await _send_add_result(callback, uid, data.get("result", 0), data.get("new_id", 0),
                            data.get("total", 0), data.get("game_number", ""),
                            games, pause_detected=True)


@router.callback_query(lambda c: c.data == "continue_session")
async def cb_continue_session(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    uid  = callback.from_user.id
    await state.clear()
    games = get_games(uid)
    await _send_add_result(callback, uid, data.get("result", 0), data.get("new_id", 0),
                            data.get("total", 0), data.get("game_number", ""),
                            games, pause_detected=False)


async def _send_add_result(callback, uid, result, new_id, total, game_number, games,
                            pause_detected):
    label   = f"Игра {game_number}" if game_number else f"Запись #{new_id}"
    confirm = (
        f"✅ *{label} добавлена!*\n"
        f"Выпало: *{ne(result)}* · Всего: {total}"
    )

    if len(games) >= 5:
        weights    = get_weights(uid)
        preds      = predict(games, weights, top_n=3, pause_detected=pause_detected)
        _record_all_preds(uid, preds)
        _record_strategy_preds(uid, games)
        bet_rec    = bet_recommendation(preds[0], weights) if preds else ""
        H          = shannon_entropy(games, window=25)
        alerts     = get_alerts(games)
        alert_text = ("\n\n" + "\n".join(alerts)) if alerts else ""
        pause_note = "\n_⚠️ Пауза обнаружена — серийные паттерны сброшены._" if pause_detected else ""

        full_text = (
            confirm
            + f"\n\n🎯 *Прогноз:*{pause_note}\n\n"
            + fmt_pred_brief(preds)
            + "\n\n🔁 *Top-3:*\n"
            + fmt_pred(preds)
            + f"\n\n💡 *Ставка:* {bet_rec}"
            + f"\n🌀 Энтропия: {fmt_entropy(H)}"
            + alert_text
        )
        markup = kb_after_add()
    else:
        remaining = 5 - len(games)
        full_text = (
            confirm
            + f"\n\n⏳ Нужно ещё *{remaining}* игр для прогноза.\n"
            + "_Быстрый ввод: напиши несколько чисел через пробел_"
        )
        markup = kb_after_add()

    try:
        await callback.message.edit_text(full_text, parse_mode="Markdown", reply_markup=markup)
    except Exception:
        await callback.message.answer(full_text, parse_mode="Markdown", reply_markup=markup)
    await callback.answer("✅ Сохранено!")


def _record_all_preds(uid: int, preds: list):
    if not preds:
        return
    for p in preds:
        record_prediction(uid, "combined", p["number"])


def _record_strategy_preds(uid: int, games: list[dict]):
    for strategy, number in strategy_top_predictions(games).items():
        record_prediction(uid, strategy, number)


@router.callback_query(lambda c: c.data == "cancel_add")
async def cb_cancel_add(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.edit_text("❌ Добавление отменено.", reply_markup=kb_main_menu())
    except Exception:
        await callback.message.answer("❌ Добавление отменено.", reply_markup=kb_main_menu())
    await callback.answer()
