"""
utils/helpers.py — форматирование вывода v6.

Изменения:
  - fmt_pred показывает реальную вероятность И преимущество над случайным
  - Убраны завышенные "confidence" в пользу честного "advantage"
  - Компактный формат весов стратегий
  - Улучшенный fmt_entropy с текстовым описанием
"""

import math

NUM_EMOJI = {
    0: "⚪", 1: "🔴", 2: "🔵", 3: "🟢",
    4: "🟣", 5: "🩵", 6: "🟡", 7: "🟠",
    8: "🔵", 9: "🟤", 10: "⚫",
}

STRATEGY_LABELS = {
    "statistical": "Стат",
    "ema":         "EMA",
    "serial":      "Серии",
    "markov":      "Марков",
    "zigzag":      "Зигзаг",
    "temporal":    "Время",
    "momentum":    "Момент",
}


def ne(n: int) -> str:
    """Число с эмодзи цвета."""
    return f"{NUM_EMOJI.get(n, '🔘')}{n}"


def bar(val: float, width: int = 8) -> str:
    """Текстовый прогресс-бар."""
    filled = round(max(0.0, min(1.0, val)) * width)
    return "▓" * filled + "░" * (width - filled)


def fmt_pred(preds: list[dict]) -> str:
    """
    Форматирует список прогнозов.

    Для каждого числа показывает:
    - реальную вероятность (prob %)
    - преимущество над случайным (+X%)
    - поддержку стратегий (N/7)
    - объяснение
    """
    if not preds:
        return "_Прогноз недоступен — недостаточно данных._"

    lines  = []
    medals = ["🥇", "🥈", "🥉"]

    for i, p in enumerate(preds):
        m    = medals[i] if i < len(medals) else "  "
        prob = p.get("probability", p.get("score", 0) * 100)
        adv  = p.get("effective_advantage", p.get("advantage", 0))
        strength = p.get("strength", 0)
        lift = p.get("lift", 1.0)
        sup  = p.get("supporters", 0)
        n_s  = len(p.get("strategy_scores", {})) or 7

        prob_bar = bar(prob / 100)

        # Иконка силы сигнала
        if adv >= 25:
            sig_icon = "🔥 Сильный"
        elif adv >= 12:
            sig_icon = "⚡ Умеренный"
        elif adv >= 4:
            sig_icon = "💧 Слабый"
        else:
            sig_icon = "〰️ Нет сигнала"

        # Преимущество над случайным
        adv_str = f"+{adv:.0f}%" if adv >= 1 else "≈ случайно"

        lines.append(
            f"{m} *{ne(p['number'])}* {sig_icon}\n"
            f"   `{prob_bar}` {prob:.1f}% вероятность · {adv_str} к базе\n"
            f"   Сила {strength:.0f}/100 · Lift ×{lift:.2f} · {sup}/{n_s} стратегий\n"
            f"   _{p['explanation']}_"
        )

    return "\n\n".join(lines)


def fmt_pred_brief(preds: list[dict]) -> str:
    """Короткий формат: основной номер + 2 запасных."""
    if not preds:
        return "_Прогноз недоступен._"

    top = preds[0]
    reserves = " · ".join(ne(p["number"]) for p in preds[1:3]) or "—"
    tradable = "да" if top.get("tradable") else "нет"
    stake_pct = int(float(top.get("stake_factor", 0.0)) * 100)
    mode = top.get("signal_mode", "skip")

    if mode == "number" and top.get("tradable"):
        return (
            f"🎯 *Основное число:* {ne(top['number'])}\n"
            f"📦 *Запасные:* {reserves}\n"
            f"📊 *Вероятность:* {top.get('probability', 0):.1f}% · "
            f"Сила {top.get('strength', 0):.0f}/100\n"
            f"🛡 *Фильтр сделки:* {tradable} · доля {stake_pct}%"
        )

    if mode == "parity":
        return (
            f"⚖️ *Основной сигнал:* {top.get('parity_side', '—')} "
            f"({top.get('parity_prob', 0):.1f}%)\n"
            f"👀 *Число-кандидат:* {ne(top['number'])}\n"
            f"📦 *Запасные:* {reserves}\n"
            f"📊 *Вероятность числа:* {top.get('probability', 0):.1f}% · "
            f"Сила {top.get('strength', 0):.0f}/100\n"
            f"🛡 *Фильтр сделки:* {tradable} · доля {stake_pct}%"
        )

    if mode == "zone":
        return (
            f"🧭 *Основной сигнал:* зона {top.get('zone_side', '—')} "
            f"({top.get('zone_prob', 0):.1f}%)\n"
            f"👀 *Число-кандидат:* {ne(top['number'])}\n"
            f"📦 *Запасные:* {reserves}\n"
            f"📊 *Вероятность числа:* {top.get('probability', 0):.1f}% · "
            f"Сила {top.get('strength', 0):.0f}/100\n"
            f"🛡 *Фильтр сделки:* {tradable} · доля {stake_pct}%"
        )

    if not top.get("tradable"):
        return (
            "🚫 *Основной сигнал:* пропуск\n"
            f"👀 *Кандидат для наблюдения:* {ne(top['number'])}\n"
            f"📦 *Альтернативы:* {reserves}\n"
            f"📊 *Вероятность:* {top.get('probability', 0):.1f}% · "
            f"Сила {top.get('strength', 0):.0f}/100\n"
            f"🛡 *Фильтр сделки:* {tradable} · доля {stake_pct}%"
        )
    return (
        f"👀 *Число-кандидат:* {ne(top['number'])}\n"
        f"📦 *Запасные:* {reserves}\n"
        f"📊 *Вероятность:* {top.get('probability', 0):.1f}% · "
        f"Сила {top.get('strength', 0):.0f}/100\n"
        f"🛡 *Фильтр сделки:* {tradable} · доля {stake_pct}%"
    )


def fmt_pred_compact(preds: list[dict]) -> str:
    """Короткий top-3 без длинных пояснений."""
    if not preds:
        return "_Нет сигнала._"

    lines = []
    for i, p in enumerate(preds[:3], start=1):
        adv = p.get("effective_advantage", p.get("advantage", 0))
        marker = "✅" if p.get("tradable") else "⛔"
        lines.append(
            f"{i}) {marker} {ne(p['number'])} · {p.get('probability', 0):.1f}% · edge {adv:.1f}%"
        )
    return "\n".join(lines)


def fmt_action_plan(preds: list[dict]) -> str:
    """Короткий actionable-блок: что делать прямо сейчас."""
    if not preds:
        return "🧭 *Действие:* ждать данные"

    top = preds[0]
    mode = top.get("signal_mode", "skip")
    mode_reason = top.get("signal_mode_reason", "")

    if mode == "number":
        stake = int(float(top.get("stake_factor", 0.0)) * 100)
        return f"🧭 *Действие:* число {ne(top['number'])} · доля {stake}% банка"
    if mode == "zone":
        return (
            f"🧭 *Действие:* зона *{top.get('zone_side', '—')}* "
            f"({top.get('zone_prob', 0):.1f}%)"
        )
    if mode == "parity":
        return (
            f"🧭 *Действие:* *{top.get('parity_side', '—')}* "
            f"({top.get('parity_prob', 0):.1f}%)"
        )
    return f"🧭 *Действие:* пропуск · {mode_reason}"


def fmt_pred_detail(preds: list[dict]) -> str:
    """
    Расширенный формат с детализацией по каждой стратегии.
    """
    if not preds:
        return "_Нет данных._"

    lines  = ["📝 *Детальный анализ прогноза:*\n"]
    medals = ["🥇", "🥈", "🥉"]

    for i, p in enumerate(preds):
        m    = medals[i] if i < len(medals) else "  "
        prob = p.get("probability", 0)
        adv  = p.get("effective_advantage", p.get("advantage", 0))
        strength = p.get("strength", 0)
        lift = p.get("lift", 1.0)
        sup  = p.get("supporters", 0)
        ss   = p.get("strategy_scores", {})
        n_s  = len(ss) or 7

        strat_line = "  ".join(
            f"{STRATEGY_LABELS.get(k, k[:4])}: {v:.0f}%"
            for k, v in sorted(ss.items(), key=lambda x: -x[1])
        )

        adv_str = f"+{adv:.0f}%" if adv >= 1 else "≈ случайно"

        lines.append(
            f"{m} *{ne(p['number'])}* — {prob:.1f}% · {adv_str}\n"
            f"   Сила {strength:.0f}/100 · Lift ×{lift:.2f} · {sup}/{n_s} стратегий\n"
            f"   Сделка: {'да' if p.get('tradable') else 'нет'} · доля {int(float(p.get('stake_factor',0))*100)}%\n"
            f"   `{strat_line}`\n"
            f"   _{p['explanation']}_"
        )

    return "\n\n".join(lines)


def fmt_weights(weights: dict) -> str:
    """Компактный вывод весов стратегий."""
    return "  ".join(
        f"{STRATEGY_LABELS.get(k, k[:4])}:{v*100:.0f}%"
        for k, v in sorted(weights.items(), key=lambda x: -x[1])
        if v > 0.01
    )


def fmt_entropy(h: float) -> str:
    """Форматирует энтропию с текстовым описанием уровня предсказуемости."""
    max_h = math.log2(11)  # ≈ 3.459
    ratio = h / max_h

    if ratio < 0.50:
        label = "🎯 очень низкая — паттерн сильный"
    elif ratio < 0.65:
        label = "📉 низкая — паттерн заметен"
    elif ratio < 0.80:
        label = "〰️ средняя — частичный порядок"
    elif ratio < 0.92:
        label = "📈 высокая — близко к случайному"
    else:
        label = "🎲 максимальная — чистый случай"

    return f"{h:.2f} / {max_h:.2f} — {label}"


def fmt_advantage_summary(preds: list[dict]) -> str:
    """Краткая сводка: стоит ли вообще делать ставку."""
    if not preds:
        return ""
    best = preds[0]
    adv  = best.get("effective_advantage", best.get("advantage", 0))
    if adv >= 20:
        return "📊 Преимущество значительное."
    elif adv >= 10:
        return "📊 Преимущество умеренное."
    elif adv >= 3:
        return "📊 Преимущество минимальное."
    else:
        return "📊 Явного преимущества нет — осторожнее."


def fmt_accuracy_block(stats: dict[str, dict]) -> str:
    """Компактный блок реальной точности по истории."""
    if not stats:
        return "_Точность пока не рассчитана._"

    ordered_keys = [key for key in ("50", "100", "all") if key in stats]
    lines = ["📈 *Реальная точность по базе:*"]
    for key in ordered_keys:
        row = stats[key]
        lines.append(
            f"• {row['label']}: top-1 {row['top1']:.1f}% · top-3 {row['top3']:.1f}% · ч/н {row['parity']:.1f}%"
        )
    return "\n".join(lines)
