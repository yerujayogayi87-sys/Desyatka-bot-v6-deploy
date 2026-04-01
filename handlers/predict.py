"""handlers/predict.py — прогнозы v6."""

import logging
from aiogram import Router
from aiogram.types import CallbackQuery

from database import get_games, get_weights, record_prediction, get_settings, is_allowed
from strategies import predict, bet_recommendation
from analytics import detect_pause, shannon_entropy
from keyboards import kb_predict, kb_back_main
from utils import fmt_pred, fmt_pred_detail, fmt_weights, fmt_entropy, fmt_advantage_summary

log = logging.getLogger(__name__)
router = Router()


async def _make_prediction(uid: int, pause_override: bool = False) -> tuple[str, bool]:
    games     = get_games(uid)
    settings  = get_settings(uid)
    threshold = settings.get("pause_threshold_min", 60)

    if len(games) < 5:
        remaining = 5 - len(games)
        return (
            f"⚠️ *Недостаточно данных*\n\n"
            f"Для прогноза нужно минимум 5 игр.\n"
            f"Добавь ещё *{remaining}* — и система начнёт анализ.",
            False
        )

    pause_h = detect_pause(games, threshold_minutes=threshold)
    pd      = (pause_h is not None) and not pause_override

    weights = get_weights(uid)
    preds   = predict(games, weights, top_n=3, pause_detected=pd)

    for p in preds:
        record_prediction(uid, "combined", p["number"])
    if preds:
        record_prediction(uid, "statistical", preds[0]["number"])

    bet_rec     = bet_recommendation(preds[0], weights) if preds else "—"
    H           = shannon_entropy(games, window=25)
    adv_summary = fmt_advantage_summary(preds)

    pause_note = ""
    if pd:
        pause_note = f"\n⚠️ *Пауза {pause_h} ч.* — краткосрочные паттерны сброшены.\n"

    # Основные 3 кандидата
    n_games = len(games)
    text = (
        f"🎯 *Прогноз · {n_games} игр в базе*\n"
        f"{pause_note}\n"
        f"{fmt_pred(preds)}\n\n"
        f"💡 *Ставка:* {bet_rec}\n"
        f"{adv_summary}\n\n"
        f"🌀 *Энтропия:* {fmt_entropy(H)}"
    )
    return text, pd


@router.callback_query(lambda c: c.data == "get_predict")
async def cb_get_predict(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    text, _ = await _make_prediction(uid)
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_predict())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_predict())
    await callback.answer()


@router.callback_query(lambda c: c.data == "predict_detail")
async def cb_predict_detail(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("Нет доступа.", show_alert=True)
        return
    games = get_games(uid)

    if len(games) < 5:
        await callback.answer("Нужно минимум 5 игр.", show_alert=True)
        return

    weights = get_weights(uid)
    preds   = predict(games, weights, top_n=3)
    H       = shannon_entropy(games, window=25)

    text = (
        fmt_pred_detail(preds)
        + f"\n\n🌀 *Энтропия:* {fmt_entropy(H)}\n"
        + f"⚖️ *Веса стратегий:* `{fmt_weights(weights)}`"
    )
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_predict())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_predict())
    await callback.answer()
