"""handlers/predict.py — прогнозы v6."""

import logging
from aiogram import Router
from aiogram.types import CallbackQuery

from database import get_games, get_weights, record_prediction, get_settings, is_allowed, get_recent_miss_counts
from strategies import predict, bet_recommendation, strategy_top_predictions, evaluate_history_predictions
from analytics import detect_pause, shannon_entropy
from keyboards import kb_predict, kb_back_main
from utils import (
    fmt_pred_brief, fmt_pred_compact, fmt_action_plan, fmt_pred_detail, fmt_weights,
    fmt_entropy, fmt_accuracy_block,
)

log = logging.getLogger(__name__)
router = Router()


def _record_prediction_set(uid: int, games: list[dict], preds: list[dict]) -> None:
    if not preds:
        return
    if preds[0].get("tradable"):
        for p in preds:
            record_prediction(uid, "combined", p["number"])
    for strategy, number in strategy_top_predictions(games).items():
        record_prediction(uid, strategy, number)


def _quality_guard(accuracy: dict[str, dict]) -> tuple[bool, str]:
    """Оценивает, пригодна ли модель к активной ставке на текущей базе."""
    if not accuracy:
        return False, "Недостаточно истории для проверки качества модели."

    windows = [accuracy[k] for k in ("50", "100") if k in accuracy]
    if not windows and "all" in accuracy:
        windows = [accuracy["all"]]
    if not windows:
        return False, "Нет валидных окон оценки качества."

    avg_top1 = sum(w.get("top1", 0.0) for w in windows) / len(windows)
    avg_top3 = sum(w.get("top3", 0.0) for w in windows) / len(windows)
    avg_parity = sum(w.get("parity", 0.0) for w in windows) / len(windows)

    # База случайного уровня: top-1=9.1%, top-3=27.3%
    if avg_top1 < 11.0 and avg_top3 < 30.0 and avg_parity < 54.0:
        return False, (
            f"Модель около случайного уровня (top-1 {avg_top1:.1f}%, top-3 {avg_top3:.1f}%, parity {avg_parity:.1f}%)."
        )
    return True, "OK"


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
    recent_misses = get_recent_miss_counts(uid)
    preds   = predict(games, weights, top_n=3, pause_detected=pd, recent_miss_counts=recent_misses)
    accuracy = evaluate_history_predictions(games, sample_sizes=(50, 100))
    quality_ok, quality_reason = _quality_guard(accuracy)

    _record_prediction_set(uid, games, preds)

    bet_rec     = bet_recommendation(preds[0], weights) if preds else "—"
    if preds and (not quality_ok) and preds[0].get("signal_mode") == "number":
        bet_rec = f"🔴 Пропустить — {quality_reason}"
    H           = shannon_entropy(games, window=25)
    pause_note = ""
    if pd:
        pause_note = f"\n⚠️ *Пауза {pause_h} ч.* — краткосрочные паттерны сброшены.\n"

    # Основные 3 кандидата
    n_games = len(games)
    text = (
        f"🎯 *Прогноз · {n_games} игр в базе*\n"
        f"{pause_note}\n"
        f"🧪 *Контроль качества:* {'проходит' if quality_ok else 'не проходит'}\n"
        f"{fmt_pred_brief(preds)}\n\n"
        f"{fmt_action_plan(preds)}\n"
        f"🔁 *Top-3:*\n{fmt_pred_compact(preds)}\n\n"
        f"{fmt_accuracy_block(accuracy)}\n"
        f"💡 *Ставка:* {bet_rec}\n"
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
    recent_misses = get_recent_miss_counts(uid)
    preds   = predict(games, weights, top_n=3, recent_miss_counts=recent_misses)
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


@router.callback_query(lambda c: c.data == "predict_audit")
async def cb_predict_audit(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("Нет доступа.", show_alert=True)
        return

    games = get_games(uid)
    if len(games) < 20:
        await callback.answer("Для аудита нужно минимум 20 игр.", show_alert=True)
        return

    weights = get_weights(uid)
    recent_misses = get_recent_miss_counts(uid)
    preds = predict(games, weights, top_n=3, recent_miss_counts=recent_misses)
    accuracy = evaluate_history_predictions(games, sample_sizes=(50, 100, None))
    quality_ok, quality_reason = _quality_guard(accuracy)
    top = preds[0] if preds else {}

    text = (
        "🔍 *Аудит прогноза*\n\n"
        f"Игр: *{len(games)}*\n"
        f"Режим: *{top.get('regime', '—')}*\n"
        f"Качество: *{'проходит' if quality_ok else 'не проходит'}*\n"
        f"Причина: {quality_reason}\n\n"
        f"{fmt_action_plan(preds)}\n"
        f"Сигнал: `{top.get('signal_mode', 'skip')}` · {top.get('signal_mode_reason', '—')}\n"
        f"Top-число: {top.get('number', '—')} · edge {top.get('effective_advantage', 0):.1f}%\n"
        f"Зона: {top.get('zone_side', '—')} ({top.get('zone_prob', 0):.1f}%)\n"
        f"Чёт/Нечет: {top.get('parity_side', '—')} ({top.get('parity_prob', 0):.1f}%)\n\n"
        f"{fmt_accuracy_block(accuracy)}"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_predict())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_predict())
    await callback.answer()
