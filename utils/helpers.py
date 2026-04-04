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
        adv  = p.get("advantage", 0)
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
    return (
        f"🎯 *Основное число:* {ne(top['number'])}\n"
        f"📦 *Запасные:* {reserves}\n"
        f"📊 *Вероятность:* {top.get('probability', 0):.1f}% · "
        f"Сила {top.get('strength', 0):.0f}/100"
    )


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
        adv  = p.get("advantage", 0)
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
    adv  = best.get("advantage", 0)
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
