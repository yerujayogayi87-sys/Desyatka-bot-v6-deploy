"""
handlers/stats.py — статистика, графики, бэктестинг, сессии,
                    горячая полоса, автопилот, калибровка, экспорт v5.
"""

import logging
import os
import tempfile
import sqlite3
import math
from datetime import datetime
from aiogram import Router
from aiogram.types import CallbackQuery, FSInputFile, BufferedInputFile

from database import (
    get_games, get_total, get_strategy_accuracy, get_weights,
    export_csv, export_json_file, is_allowed,
)
from analytics import (
    frequency, hot_cold, gap_analysis, even_odd_streak,
    range_stats, find_runs, time_analysis, get_alerts,
    make_frequency_chart, make_even_odd_chart,
    make_history_heatmap, make_strategy_radar,
    shannon_entropy,
)
from strategies import predict, bet_recommendation
from keyboards import kb_stats, kb_back_main
from utils import ne, bar, fmt_entropy

log = logging.getLogger(__name__)
router = Router()


# ── Статистика (текст) ────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "stats")
async def cb_stats(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    total = len(games)

    if total == 0:
        await callback.message.edit_text(
            "📊 База пуста. Добавь игры через меню.",
            reply_markup=kb_back_main(),
        )
        await callback.answer()
        return

    freq      = frequency(games)
    hot, cold = hot_cold(games)
    gaps      = gap_analysis(games)
    eo        = even_odd_streak(games)
    ranges    = range_stats(games)
    runs      = find_runs(games)
    ta        = time_analysis(games)
    H         = shannon_entropy(games, window=min(30, total))

    max_cnt = max(freq.values()) or 1
    hist_lines = []
    for num in range(11):
        cnt = freq[num]
        pct = cnt / total * 100
        b   = bar(cnt / max_cnt, width=12)
        gap = gaps.get(num, 0)
        ago = f"{gap}↑" if gap > 0 else "←"
        hist_lines.append(f"`{ne(num)}` `{b}` {cnt:>3}× {pct:4.1f}%  {ago}")

    hist   = "\n".join(hist_lines)
    hot_s  = " ".join(ne(n) for n in hot)
    cold_s = " ".join(ne(n) for n in cold)

    eo_alert = ""
    if eo.get("current_streak", 0) >= 4:
        eo_alert = f"\n⚠️ Серия {eo['current_parity']} × {eo['current_streak']}"

    text = (
        f"📊 *Статистика* ({total} игр)\n\n"
        f"{hist}\n\n"
        f"🔥 Горячие: {hot_s}  ❄️ Холодные: {cold_s}\n\n"
        f"⚖️ Чётные {eo.get('even',0)} ({eo.get('even_pct',0):.0f}%) · "
        f"Нечётные {eo.get('odd',0)} ({eo.get('odd_pct',0):.0f}%){eo_alert}\n\n"
        f"📐 0–3: {ranges['low'][0]}× ({ranges['low'][1]:.0f}%)  "
        f"4–7: {ranges['mid'][0]}× ({ranges['mid'][1]:.0f}%)  "
        f"8–10: {ranges['high'][0]}× ({ranges['high'][1]:.0f}%)\n\n"
        f"📈 Серия: {ne(runs.get('current_value',0))} ×{runs.get('current_length',0)}  "
        f"Рекорд: {ne(runs.get('max_value',0))} ×{runs.get('max_length',0)}\n\n"
        f"🌀 Энтропия: {fmt_entropy(H)}\n"
        f"🕐 {ta['current_dow']} {ta['current_hour']}:xx · "
        f"горячие: {' '.join(ne(n) for n in ta['hour_top']) or '—'}"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


# ── График частот ─────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "stats_chart")
async def cb_stats_chart(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 3:
        await callback.answer("Нужно хотя бы 3 игры.", show_alert=True)
        return
    await callback.answer("⏳ Строю график…")
    buf   = make_frequency_chart(games)
    photo = BufferedInputFile(buf.read(), filename="freq_chart.png")
    await callback.message.answer_photo(
        photo,
        caption=f"📊 *Частота чисел* · {len(games)} игр\nКрасная линия — равномерное распределение",
        parse_mode="Markdown",
        reply_markup=kb_stats(),
    )


# ── Тепловая карта ────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "heatmap")
async def cb_heatmap(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 5:
        await callback.answer("Нужно хотя бы 5 игр.", show_alert=True)
        return
    await callback.answer("⏳ Строю карту…")
    buf   = make_history_heatmap(games)
    photo = BufferedInputFile(buf.read(), filename="heatmap.png")
    await callback.message.answer_photo(
        photo,
        caption="🌡️ *Тепловая карта* — зелёная клетка = число выпало\n→ самая свежая игра справа",
        parse_mode="Markdown",
        reply_markup=kb_stats(),
    )


# ── График чет/нечет ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "even_odd_chart")
async def cb_even_odd_chart(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 5:
        await callback.answer("Нужно хотя бы 5 игр.", show_alert=True)
        return
    await callback.answer("⏳ Строю график…")
    buf   = make_even_odd_chart(games)
    photo = BufferedInputFile(buf.read(), filename="even_odd.png")
    eo    = even_odd_streak(games)
    await callback.message.answer_photo(
        photo,
        caption=(
            f"⚖️ *Чёт / Нечет* · {len(games)} игр\n"
            f"Серия: *{eo.get('current_parity','?')} × {eo.get('current_streak',0)}*"
        ),
        parse_mode="Markdown",
        reply_markup=kb_stats(),
    )


# ── Прогноз чет/нечет ────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "predict_even_odd")
async def cb_predict_even_odd(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 5:
        await callback.answer("Нужно минимум 5 игр.", show_alert=True)
        return

    eo       = even_odd_streak(games)
    even_p   = eo.get("even_pct", 50)
    odd_p    = eo.get("odd_pct", 50)
    streak   = eo.get("current_streak", 0)
    parity   = eo.get("current_parity", "?")
    opposite = "нечётное" if parity == "чётное" else "чётное"

    # Взвешенный сигнал: статистика + серийный откат
    streak_weight = min(streak / 8.0, 0.6)
    stat_even = even_p / 100
    stat_odd  = odd_p  / 100

    if parity == "чётное":
        adj_odd  = stat_odd  + streak_weight * (1 - stat_odd)
        adj_even = 1.0 - adj_odd
    else:
        adj_even = stat_even + streak_weight * (1 - stat_even)
        adj_odd  = 1.0 - adj_even

    s = adj_even + adj_odd
    adj_even /= s
    adj_odd  /= s

    if adj_even >= adj_odd:
        winner = "чётное"
        conf   = round(adj_even * 100, 1)
    else:
        winner = "нечётное"
        conf   = round(adj_odd * 100, 1)

    if conf >= 65:
        rec = "🟢 Уверенная"
    elif conf >= 56:
        rec = "🟡 Осторожная"
    else:
        rec = "🔴 Неоднозначно"

    text = (
        f"⚖️ *Прогноз чёт / нечет*\n\n"
        f"Чётных: {eo.get('even',0)}× ({even_p:.0f}%)  "
        f"Нечётных: {eo.get('odd',0)}× ({odd_p:.0f}%)\n"
        f"Серия: *{parity} × {streak}*\n\n"
        f"🎯 *Прогноз: {winner.upper()}*\n"
        f"Уверенность: {conf:.0f}% — {rec}\n\n"
        f"_Учитывает распределение + длину текущей серии_"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


# ── Бэктестинг ───────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "compare")
async def cb_compare(callback: CallbackQuery):
    uid     = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    acc     = get_strategy_accuracy(uid)
    weights = get_weights(uid)

    if not acc:
        await callback.message.edit_text(
            "⏳ Данных пока нет. Добавь несколько игр.",
            reply_markup=kb_back_main(),
        )
        await callback.answer()
        return

    lines = ["📊 *Бэктестинг стратегий:*\n"]
    for s, data in sorted(acc.items(), key=lambda x: -(x[1]["correct"] / max(x[1]["total"], 1))):
        tot     = data["total"]
        correct = data["correct"]
        pct     = correct / tot * 100 if tot else 0
        w       = weights.get(s, 0.0)
        b       = bar(pct / 100)
        lines.append(
            f"*{s}*\n"
            f"  `{b}` {pct:.0f}% ({correct}/{tot})\n"
            f"  Вес: {w*100:.0f}%"
        )

    db_path = os.getenv("DB_PATH", "games.db")
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) as t, SUM(correct) as c FROM predictions "
            "WHERE user_id=? AND strategy='combined' AND actual IS NOT NULL",
            (uid,)
        )
        row = cur.fetchone()
        con.close()
        if row and row["t"]:
            tot_c = row["t"]
            cor_c = row["c"] or 0
            pct_c = cor_c / tot_c * 100
            b_c   = bar(pct_c / 100)
            lines.append(
                f"*combined (итог top-3)*\n"
                f"  `{b_c}` {pct_c:.0f}% ({cor_c}/{tot_c})"
            )
    except Exception:
        pass

    text = "\n\n".join(lines)
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


# ── Сессии ───────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "sessions")
async def cb_sessions(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 2:
        await callback.answer("Нужно хотя бы 2 игры.", show_alert=True)
        return

    sessions = _split_sessions(games)
    if not sessions:
        await callback.answer("Сессии не обнаружены.", show_alert=True)
        return

    lines = [f"🗂 *Сессии* (найдено: {len(sessions)})\n"]
    for i, sess in enumerate(sessions[:6], 1):
        start  = sess[-1].get("added_at", "")[:16]
        end    = sess[0].get("added_at", "")[:16]
        cnt    = len(sess)
        freq   = frequency(sess)
        top3   = sorted(freq.items(), key=lambda x: -x[1])[:3]
        top3_s = " ".join(f"{ne(n)}×{c}" for n, c in top3 if c > 0)
        eo     = even_odd_streak(sess)
        H      = shannon_entropy(sess, window=cnt)
        lines.append(
            f"*Сессия {i}* ({cnt} игр)\n"
            f"  {start} → {end}\n"
            f"  🔝 {top3_s}\n"
            f"  ⚖️ Ч:{eo.get('even_pct',0):.0f}% Н:{eo.get('odd_pct',0):.0f}%  "
            f"🌀 H={H:.2f}"
        )
    if len(sessions) > 6:
        lines.append(f"_…и ещё {len(sessions)-6} сессий_")

    text = "\n\n".join(lines)
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


def _split_sessions(games: list[dict], gap_minutes: int = 60) -> list[list[dict]]:
    if not games:
        return []
    sessions, current = [], [games[0]]
    for i in range(1, len(games)):
        try:
            t1   = datetime.fromisoformat(games[i - 1]["added_at"])
            t2   = datetime.fromisoformat(games[i]["added_at"])
            diff = (t1 - t2).total_seconds() / 60
        except Exception:
            diff = 0
        if diff >= gap_minutes:
            sessions.append(current)
            current = []
        current.append(games[i])
    if current:
        sessions.append(current)
    return sessions


# ── Горячая полоса ────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "hot_streak")
async def cb_hot_streak(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 6:
        await callback.answer("Нужно минимум 6 игр.", show_alert=True)
        return

    weights     = get_weights(uid)
    window      = min(25, len(games) - 1)
    streak_data = []

    for i in range(window):
        hist   = games[i + 1:]
        if len(hist) < 5:
            break
        actual = games[i]["result"]
        preds  = predict(hist, weights, top_n=3)
        hit    = actual in [p["number"] for p in preds]
        streak_data.append((actual, hit))

    if not streak_data:
        await callback.answer("Недостаточно данных.", show_alert=True)
        return

    cur_type   = streak_data[0][1]
    cur_streak = 0
    for _, hit in streak_data:
        if hit == cur_type:
            cur_streak += 1
        else:
            break

    total_hits    = sum(1 for _, h in streak_data if h)
    total_checked = len(streak_data)
    accuracy      = total_hits / total_checked * 100

    visual = "".join("✅" if h else "❌" for _, h in streak_data[:20])
    streak_emoji = "🔥" if cur_type else "❄️"
    streak_label = "угаданных" if cur_type else "неугаданных"

    # Байесовская оценка реальной точности с 95% CI
    p_hat = total_hits / total_checked
    margin = 1.96 * math.sqrt(p_hat * (1 - p_hat) / total_checked)
    ci_lo  = max(0, p_hat - margin) * 100
    ci_hi  = min(100, p_hat + margin) * 100

    text = (
        f"🎯 *Горячая полоса*\n\n"
        f"`{visual}`\n\n"
        f"{streak_emoji} Серия: *{cur_streak} {streak_label} подряд*\n\n"
        f"📊 Точность top-3: *{accuracy:.0f}%* ({total_hits}/{total_checked})\n"
        f"95% CI: [{ci_lo:.0f}% – {ci_hi:.0f}%]\n\n"
        f"_✅ = попал в top-3  ·  ❌ = не угадал_"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


# ── Автопилот ─────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "autopilot")
async def cb_autopilot(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 10:
        await callback.answer("Нужно минимум 10 игр.", show_alert=True)
        return

    weights = get_weights(uid)
    preds   = predict(games, weights, top_n=3)
    if not preds:
        await callback.answer("Не удалось построить прогноз.", show_alert=True)
        return

    top        = preds[0]
    conf       = top["confidence"]
    lift       = top.get("lift", 1.0)
    supporters = top.get("supporters", 0)
    ss         = top.get("strategy_scores", {})
    agreement  = sum(1 for v in ss.values() if v > 9)
    H          = shannon_entropy(games, window=25)
    h_ratio    = H / math.log2(11)

    # Логика решения с учётом энтропии
    if conf >= 55 and agreement >= 4 and lift >= 1.8 and h_ratio < 0.85:
        decision = "✅ СТАВИТЬ"
        reason   = "высокий консенсус + хороший lift + паттерн активен"
        color    = "🟢"
    elif conf >= 38 and (agreement >= 3 or lift >= 1.4):
        decision = "⚡ ОСТОРОЖНО"
        reason   = "умеренный сигнал"
        color    = "🟡"
    elif h_ratio > 0.90:
        decision = "🚫 ПРОПУСТИТЬ"
        reason   = "высокая энтропия — близко к случайному"
        color    = "🔴"
    elif agreement <= 1:
        decision = "🚫 ПРОПУСТИТЬ"
        reason   = "стратегии не согласованы"
        color    = "🔴"
    else:
        decision = "⚡ НА УСМОТРЕНИЕ"
        reason   = "сигналы смешанные"
        color    = "🟠"

    top_nums = " ".join(ne(p["number"]) for p in preds)

    text = (
        f"🤖 *Автопилот*\n\n"
        f"{color} *{decision}*\n"
        f"_{reason}_\n\n"
        f"🎯 Числа: {top_nums}\n"
        f"📊 Уверенность: {conf:.0f}%  Lift: ×{lift:.2f}\n"
        f"🤝 Консенсус: {agreement}/6 стратегий\n"
        f"🌀 Энтропия: {fmt_entropy(H)}\n\n"
        f"_Пропускает если энтропия > 90% или консенсус ≤ 1_"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


# ── Калибровка ────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "calibration")
async def cb_calibration(callback: CallbackQuery):
    uid   = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games = get_games(uid)
    if len(games) < 50:
        await callback.answer(
            f"Нужно 50+ игр. Есть: {len(games)}, осталось: {50-len(games)}.",
            show_alert=True,
        )
        return

    weights = get_weights(uid)
    n_check = min(60, len(games) - 5)
    buckets = {
        "high":   {"claimed": [], "hits": 0, "total": 0},
        "medium": {"claimed": [], "hits": 0, "total": 0},
        "low":    {"claimed": [], "hits": 0, "total": 0},
    }

    for i in range(n_check):
        hist   = games[i + 1:]
        actual = games[i]["result"]
        if len(hist) < 5:
            break
        preds = predict(hist, weights, top_n=3)
        if not preds:
            continue
        top_conf = preds[0]["confidence"]
        hit = actual in [p["number"] for p in preds]

        if top_conf >= 55:
            b = buckets["high"]
        elif top_conf >= 30:
            b = buckets["medium"]
        else:
            b = buckets["low"]

        b["claimed"].append(top_conf)
        b["total"] += 1
        if hit:
            b["hits"] += 1

    lines = ["🔬 *Калибровка модели*\n_(заявленная уверенность vs реальная точность)_\n"]
    labels = {
        "high":   "🟢 Высокая (≥55%)",
        "medium": "🟡 Средняя (30–54%)",
        "low":    "🔴 Низкая (<30%)",
    }

    for key, label in labels.items():
        b = buckets[key]
        if b["total"] == 0:
            lines.append(f"{label}: нет данных")
            continue
        claimed_avg = sum(b["claimed"]) / len(b["claimed"])
        real_pct    = b["hits"] / b["total"] * 100
        delta       = real_pct - claimed_avg
        delta_s     = f"+{delta:.0f}%" if delta >= 0 else f"{delta:.0f}%"
        cal_emoji   = "✅" if abs(delta) < 12 else ("⬆️" if delta > 0 else "⬇️")
        lines.append(
            f"{label}\n"
            f"  Заявлено: {claimed_avg:.0f}%  Реально: {real_pct:.0f}%\n"
            f"  Δ {delta_s} {cal_emoji}  ({b['hits']}/{b['total']})"
        )

    lines.append("\n_✅ = хорошая (±12%)  ⬆️ = недооценка  ⬇️ = переоценка_")
    text = "\n\n".join(lines)
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


# ── Алерты ───────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "alerts")
async def cb_alerts(callback: CallbackQuery):
    uid    = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    games  = get_games(uid)
    alerts = get_alerts(games)
    text   = ("⚠️ *Предупреждения:*\n\n" + "\n".join(alerts)) if alerts \
             else "✅ Тревожных паттернов не обнаружено."
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_stats())
    except Exception:
        await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb_stats())
    await callback.answer()


# ── Экспорт ───────────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "export")
async def cb_export(callback: CallbackQuery):
    uid = callback.from_user.id
    if not is_allowed(uid):
        await callback.answer("🔒 Доступ закрыт.", show_alert=True)
        return
    await callback.answer("⏳ Готовлю файлы...")
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path  = os.path.join(tmpdir, "desyatka_export.csv")
        json_path = os.path.join(tmpdir, "desyatka_export.json")
        n = export_csv(uid, csv_path)
        export_json_file(uid, json_path)
        if n == 0:
            await callback.message.answer("База пуста — нечего экспортировать.")
            return
        await callback.message.answer_document(
            FSInputFile(csv_path, filename="desyatka_export.csv"),
            caption=f"📊 CSV — {n} записей",
        )
        await callback.message.answer_document(
            FSInputFile(json_path, filename="desyatka_export.json"),
            caption="📋 JSON",
        )
        await callback.message.answer(
            f"✅ Экспортировано {n} записей.", reply_markup=kb_back_main()
        )
