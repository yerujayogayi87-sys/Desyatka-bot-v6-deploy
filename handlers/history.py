"""handlers/history.py — история игр с пагинацией и удалением."""

import logging
from aiogram import Router
from aiogram.types import CallbackQuery

from database import is_allowed, get_games, delete_game_by_id
from keyboards import kb_history, kb_game_detail, kb_back_main
from utils import ne

log = logging.getLogger(__name__)
router = Router()
PER_PAGE = 10


@router.callback_query(lambda c: c.data == "history")
async def cb_history(callback: CallbackQuery):
    await _show_history(callback, page=0)


@router.callback_query(lambda c: c.data and c.data.startswith("hist_page_"))
async def cb_hist_page(callback: CallbackQuery):
    try:
        page = int(callback.data.split("_")[-1])
    except ValueError:
        page = 0
    await _show_history(callback, page=page)


async def _show_history(callback: CallbackQuery, page: int):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)

    if not games:
        await callback.message.edit_text("📜 История пуста.", reply_markup=kb_back_main())
        await callback.answer()
        return

    total_pages = (len(games) - 1) // PER_PAGE + 1
    text = (
        f"📜 *История игр* ({len(games)} всего · стр. {page+1}/{total_pages})\n"
        f"Нажми запись для деталей:"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=kb_history(games, page=page, per_page=PER_PAGE),
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="Markdown",
            reply_markup=kb_history(games, page=page, per_page=PER_PAGE),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("game_detail_"))
async def cb_game_detail(callback: CallbackQuery):
    try:
        game_id = int(callback.data.split("_")[-1])
    except ValueError:
        await callback.answer("Ошибка.", show_alert=True)
        return

    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    game  = next((g for g in games if g["id"] == game_id), None)

    if not game:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    dt  = (game.get("added_at") or "")[:19]
    gn  = game.get("game_number") or "—"
    dow = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][game["day_of_week"]] \
          if game.get("day_of_week") is not None else "—"

    text = (
        f"📋 *Запись #{game_id}*\n\n"
        f"🎲 Число: *{ne(game['result'])}*\n"
        f"📅 Дата: `{dt}`\n"
        f"📆 День: {dow}, {game.get('hour_of_day','—')}:xx\n"
        f"🔢 Номер игры: `{gn}`"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown", reply_markup=kb_game_detail(game_id)
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="Markdown", reply_markup=kb_game_detail(game_id)
        )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("delete_game_"))
async def cb_delete_game(callback: CallbackQuery):
    try:
        game_id = int(callback.data.split("_")[-1])
    except ValueError:
        await callback.answer("Ошибка.", show_alert=True)
        return

    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    ok  = delete_game_by_id(uid, game_id)

    if not ok:
        await callback.answer("Запись не найдена.", show_alert=True)
        return

    await callback.answer("✅ Удалено.")
    games = get_games(uid)
    if not games:
        await callback.message.edit_text("📜 История пуста.", reply_markup=kb_back_main())
        return

    text = f"📜 *История игр* ({len(games)} всего):"
    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown",
            reply_markup=kb_history(games, page=0, per_page=PER_PAGE),
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="Markdown",
            reply_markup=kb_history(games, page=0, per_page=PER_PAGE),
        )
