"""handlers/settings.py — настройки бота v6 (7 стратегий включая recency)."""

import logging
from aiogram import Router
from aiogram.types import CallbackQuery

from database import is_allowed, get_settings, save_settings, clear_games
from keyboards import kb_settings, kb_clear_confirm, kb_back_main, kb_pause_threshold

log = logging.getLogger(__name__)
router = Router()


async def _show_settings(callback: CallbackQuery):
    uid      = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    settings  = get_settings(uid)
    threshold = settings.get("pause_threshold_min", 60)

    text = (
        "⚙️ *Настройки*\n\n"
        f"⏱ Порог паузы: *{threshold} мин*\n\n"
        "Стратегии прогнозирования (нажми чтобы вкл/выкл):\n"
        "_Каждая стратегия анализирует данные по-своему.\n"
        "Все включены по умолчанию — рекомендуется не отключать._"
    )
    try:
        await callback.message.edit_text(
            text, parse_mode="Markdown", reply_markup=kb_settings(settings)
        )
    except Exception:
        await callback.message.answer(
            text, parse_mode="Markdown", reply_markup=kb_settings(settings)
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "settings")
async def cb_settings(callback: CallbackQuery):
    await _show_settings(callback)


# ── Переключатели стратегий ───────────────────────────────────────────────────

STRATS = ("statistical", "ema", "serial", "markov", "zigzag", "temporal", "recency")

for _strat in STRATS:
    async def _toggle(callback: CallbackQuery, strat=_strat):
        uid = callback.from_user.id
        if not is_allowed(uid):
            await callback.answer("🔒 Доступ закрыт.", show_alert=True)
            return
        settings = get_settings(uid)
        key      = f"use_{strat}"
        current  = settings.get(key, 1)
        save_settings(uid, **{key: 0 if current else 1})
        await _show_settings(callback)

    _toggle.__name__ = f"cb_toggle_{_strat}"
    router.callback_query(lambda c, s=_strat: c.data == f"toggle_{s}")(_toggle)


# ── Порог паузы ───────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "set_pause_threshold")
async def cb_set_pause_threshold(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            "⏱ *Порог паузы*\n\n"
            "Если между двумя играми прошло больше этого времени,\n"
            "краткосрочные паттерны сбрасываются.",
            parse_mode="Markdown",
            reply_markup=kb_pause_threshold(),
        )
    except Exception:
        await callback.message.answer(
            "⏱ Выберите порог паузы:",
            reply_markup=kb_pause_threshold(),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("pause_"))
async def cb_pause_value(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    try:
        minutes = int(callback.data.split("_")[1])
    except ValueError:
        await callback.answer("Ошибка.", show_alert=True)
        return
    save_settings(uid, pause_threshold_min=minutes)
    await callback.answer(f"✅ Порог установлен: {minutes} мин.")
    await _show_settings(callback)


# ── Очистка данных ────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "clear_confirm")
async def cb_clear_confirm(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            "⚠️ *Внимание!*\n\n"
            "Это удалит *все* ваши игры, статистику и веса стратегий.\n"
            "Действие необратимо.\n\n"
            "Вы уверены?",
            parse_mode="Markdown",
            reply_markup=kb_clear_confirm(),
        )
    except Exception:
        await callback.message.answer(
            "⚠️ Удалить все данные?",
            parse_mode="Markdown",
            reply_markup=kb_clear_confirm(),
        )
    await callback.answer()


@router.callback_query(lambda c: c.data == "clear_do")
async def cb_clear_do(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    clear_games(uid)
    try:
        await callback.message.edit_text(
            "✅ *База очищена.*\nВсе игры и статистика удалены.",
            parse_mode="Markdown",
            reply_markup=kb_back_main(),
        )
    except Exception:
        await callback.message.answer("✅ База очищена.", reply_markup=kb_back_main())
    await callback.answer("✅ Очищено!")
