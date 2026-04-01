"""
strategies/predictors.py — движок предсказаний v6.

Ключевые улучшения над v5:
  1. Честная калибровка уверенности — убраны завышенные x3.5 мультипликаторы
  2. Softmax temperature=0.7 вместо 0.4 — менее агрессивное заострение
  3. Serial стратегия: Лапласово сглаживание + точнее работает с малой историей
  4. Markov: fallback до order-1 если нет совпадений, а не uniform
  5. Agreement boost только при поддержке 5+/6 стратегий (ранее 4+/6)
  6. Gap-бонус ограничен: не даёт искусственно завышать "давно не выпавшие"
  7. Новая стратегия: momentum — тренд по последним 5/10/20 играм
  8. Temporal: только при достаточной выборке (>=10, ранее 5)
  9. Вывод: честный процент вместо "уверенность 87%"
 10. bet_recommendation пересчитан под реальные пороги
"""

import math
from collections import Counter
from analytics import (
    frequency, gap_analysis, markov_predict, markov_predict_order3,
    time_analysis, deviation_scores, alternation_score, even_odd_streak,
    ema_scores, zigzag_score, shannon_entropy, cluster_trend,
)

ScoreMap = dict[int, float]
NUMS     = list(range(11))
UNIFORM  = 1 / 11           # базовая вероятность равномерного распределения


# ── Нормализация ──────────────────────────────────────────────────────────────

def _normalize(scores: ScoreMap) -> ScoreMap:
    total = sum(scores.values())
    if not total:
        return {i: UNIFORM for i in NUMS}
    return {k: v / total for k, v in scores.items()}


def _softmax(scores: ScoreMap, temperature: float = 0.7) -> ScoreMap:
    """
    Softmax с температурой.
    temperature=0.7 — умеренное заострение без экстремального давления.
    temperature=1.0 — пропорциональное распределение.
    """
    vals  = {k: v / temperature for k, v in scores.items()}
    max_v = max(vals.values())
    exps  = {k: math.exp(v - max_v) for k, v in vals.items()}
    total = sum(exps.values())
    return {k: v / total for k, v in exps.items()}


def _uniform() -> ScoreMap:
    return {i: UNIFORM for i in NUMS}


# ── Стратегия 1: Статистическая ───────────────────────────────────────────────

def strat_statistical(games: list[dict]) -> ScoreMap:
    """
    Взвешенная частота (60% последние 30 + 40% вся история) + gap-бонус.
    Gap ограничен: не более +50% к базовому весу, чтобы не доминировал.
    """
    if not games:
        return _uniform()

    total_all  = len(games)
    recent_n   = min(30, total_all)
    recent     = games[:recent_n]
    total_rec  = len(recent)

    freq_all    = frequency(games)
    freq_recent = frequency(recent)
    dev         = deviation_scores(games)
    gaps        = gap_analysis(games)

    # Ожидаемый gap при равномерном распределении
    expected_gap = total_all / 11

    scores = {}
    for num in NUMS:
        s_all = (freq_all[num] / total_all) * 0.4
        s_rec = (freq_recent[num] / total_rec) * 0.6

        # Gap-бонус: нормируем к ожидаемому, ограничиваем сверху
        gap_raw   = gaps.get(num, 0)
        gap_norm  = gap_raw / max(expected_gap, 1)
        gap_bonus = min(math.sqrt(max(gap_norm - 1, 0)) * 0.15, 0.10)

        # Deviation: штрафуем перегретые числа
        dev_score = max(-dev.get(num, 0), 0) * 0.08

        scores[num] = s_all + s_rec + gap_bonus + dev_score

    return _normalize(scores)


# ── Стратегия 2: EMA-тренд ────────────────────────────────────────────────────

def strat_ema(games: list[dict]) -> ScoreMap:
    """
    MACD-аналог: fast EMA (alpha=0.25) vs slow EMA (alpha=0.08).
    Сигнал = текущий тренд каждого числа.
    При малой истории (<8 игр) возвращает uniform.
    """
    if len(games) < 8:
        return _uniform()

    fast = ema_scores(games, alpha=0.25)
    slow = ema_scores(games, alpha=0.08)

    scores = {}
    for num in NUMS:
        macd         = fast[num] - slow[num]
        trend_signal = fast[num] + macd * 1.5   # сигнал тренда
        scores[num]  = max(trend_signal, 1e-6)

    return _normalize(scores)


# ── Стратегия 3: Серийная ─────────────────────────────────────────────────────

def strat_serial(games: list[dict]) -> ScoreMap:
    """
    Контекстные паттерны: ищет последовательности длиной 3, затем 2.
    Лапласово сглаживание для надёжных вероятностей.
    Fallback: чётность/нечётность с адаптивной силой.
    """
    if len(games) < 6:
        return _uniform()

    results_asc = list(reversed([g["result"] for g in games]))

    for ctx_len in (3, 2):
        if len(results_asc) < ctx_len + 1:
            continue
        context = tuple(results_asc[-ctx_len:])
        follow: dict[int, int] = {}
        for i in range(len(results_asc) - ctx_len):
            if tuple(results_asc[i: i + ctx_len]) == context:
                nxt = results_asc[i + ctx_len]
                follow[nxt] = follow.get(nxt, 0) + 1

        total_hits = sum(follow.values())
        # Требуем минимум 3 вхождения паттерна для доверия
        if total_hits >= 3:
            # Сглаживание Лапласа: alpha = 0.5 / (total_hits) — слабее при большой выборке
            alpha = max(0.3, 1.0 / (total_hits + 1))
            denom = total_hits + alpha * 11
            scores = {i: (follow.get(i, 0) + alpha) / denom for i in NUMS}
            return _normalize(scores)

    # Fallback: чётность/нечётность
    eo     = even_odd_streak(games)
    streak = eo.get("current_streak", 0)
    parity = games[0]["result"] % 2

    scores = {}
    for num in NUMS:
        if streak >= 5:
            # Сильная серия → предпочитаем противоположное
            scores[num] = 2.5 if num % 2 != parity else 0.4
        elif streak >= 3:
            scores[num] = 1.6 if num % 2 != parity else 0.6
        else:
            scores[num] = 1.0
    return _normalize(scores)


# ── Стратегия 4: Марков ───────────────────────────────────────────────────────

def _markov_with_decay(games: list[dict], order: int = 2, half_life: int = 20) -> dict[int, float]:
    """
    Марковские переходы с временным decay: свежие переходы весят больше.
    half_life — количество игр до снижения веса вдвое.
    """
    results_asc = list(reversed([g["result"] for g in games]))
    n = len(results_asc)
    counts: dict[tuple, dict[int, float]] = {}

    for i in range(n - order):
        ctx = tuple(results_asc[i: i + order])
        nxt = results_asc[i + order]
        # Вес экспоненциально убывает: самые свежие события — в конце списка
        age = (n - order - 1) - i          # 0 = самый свежий переход
        weight = math.exp(-age * math.log(2) / half_life)
        if ctx not in counts:
            counts[ctx] = {}
        counts[ctx][nxt] = counts[ctx].get(nxt, 0) + weight

    if not results_asc:
        return {}

    context = tuple(results_asc[-order:])
    if context not in counts:
        return {}

    total = sum(counts[context].values()) or 1
    return {n: w / total for n, w in counts[context].items()}


def strat_markov(games: list[dict]) -> ScoreMap:
    """
    Марковские цепи 2-го (основной) и 3-го (при >=25 играх) порядков
    с временным decay: свежие переходы весят больше старых.
    При отсутствии совпадений — fallback на order-1, а не uniform.
    """
    if len(games) < 4:
        return _uniform()

    use_order3 = len(games) >= 25
    # Используем decay-версию для order-2 (основная)
    probs2 = _markov_with_decay(games, order=2, half_life=15)
    # Order-3 с более длинным half_life для стабильности
    probs3 = _markov_with_decay(games, order=3, half_life=25) if use_order3 else {}

    if probs3 and probs2:
        probs = {n: probs3.get(n, 0) * 0.55 + probs2.get(n, UNIFORM) * 0.45
                 for n in NUMS}
    elif probs2:
        probs = probs2
    elif probs3:
        probs = probs3
    else:
        # Fallback order-1 с decay
        probs = _markov_with_decay(games, order=1, half_life=10)
        if not probs:
            return _uniform()

    scores = {i: max(probs.get(i, UNIFORM * 0.5), 1e-6) for i in NUMS}

    # Коррекция на чередование
    alt = alternation_score(games, window=12)
    if alt > 0.75:
        last = games[0]["result"]
        for num in NUMS:
            if num == last:
                scores[num] *= 0.65
            else:
                scores[num] *= 1.05

    return _normalize(scores)


# ── Стратегия 5: Zigzag ───────────────────────────────────────────────────────

def strat_zigzag(games: list[dict]) -> ScoreMap:
    """Паттерн чередования диапазонов high↔low."""
    if len(games) < 8:
        return _uniform()
    return zigzag_score(games, window=14)


# ── Стратегия 6: Временная ────────────────────────────────────────────────────

def strat_temporal(games: list[dict]) -> ScoreMap:
    """
    Час суток + день недели.
    Минимум 10 совпадений по времени для доверия (ранее 5).
    """
    ta     = time_analysis(games)
    scores = {i: 1.0 for i in NUMS}

    if ta["hour_sample"] >= 10:
        for num in ta["hour_top"]:
            scores[num] *= 1.5
    elif ta["hour_sample"] >= 5:
        for num in ta["hour_top"]:
            scores[num] *= 1.2

    if ta["dow_sample"] >= 10:
        for num in ta["dow_top"]:
            scores[num] *= 1.35
    elif ta["dow_sample"] >= 5:
        for num in ta["dow_top"]:
            scores[num] *= 1.15

    if len(games) >= 15:
        ct = cluster_trend(games)
        for num in NUMS:
            zone  = "L" if num <= 3 else ("H" if num >= 8 else "M")
            ratio = ct.get(zone, 1.0)
            if ratio > 1.3:
                scores[num] *= 1.25
            elif ratio < 0.65:
                scores[num] *= 0.82

    return _normalize(scores)


# ── Стратегия 7: Моментум ─────────────────────────────────────────────────────

def strat_momentum(games: list[dict]) -> ScoreMap:
    """
    Тренд частоты по трём окнам: 5, 10, 20 последних игр.
    Если число набирает популярность — повышаем его вес.
    Если падает — снижаем.
    """
    if len(games) < 10:
        return _uniform()

    def window_freq(n: int) -> dict[int, float]:
        subset = games[:n]
        total  = len(subset) or 1
        return {i: frequency(subset)[i] / total for i in NUMS}

    w5  = window_freq(5)
    w10 = window_freq(min(10, len(games)))
    w20 = window_freq(min(20, len(games)))

    scores = {}
    for num in NUMS:
        # Тренд: сравниваем краткосрочное с долгосрочным
        short_trend  = w5[num]  - w10[num]
        medium_trend = w10[num] - w20[num]
        # Моментум = взвешенная сумма трендов
        momentum    = short_trend * 0.6 + medium_trend * 0.4
        base        = w10[num]
        scores[num] = max(base + momentum * 1.5, 1e-6)

    return _normalize(scores)


# ── Реестр стратегий ──────────────────────────────────────────────────────────

STRATEGY_FNS = {
    "statistical": strat_statistical,
    "ema":         strat_ema,
    "serial":      strat_serial,
    "markov":      strat_markov,
    "zigzag":      strat_zigzag,
    "temporal":    strat_temporal,
    "momentum":    strat_momentum,
}

STRATEGY_NAMES_RU = {
    "statistical": "Статистика",
    "ema":         "EMA-тренд",
    "serial":      "Серии",
    "markov":      "Марков",
    "zigzag":      "Зигзаг",
    "temporal":    "Время",
    "momentum":    "Моментум",
}


# ── Главный движок ────────────────────────────────────────────────────────────

def predict(
    games:          list[dict],
    weights:        dict[str, float],
    top_n:          int  = 3,
    pause_detected: bool = False,
) -> list[dict]:
    """
    Комбинированное предсказание.

    Адаптации:
    - pause_detected: снижаем краткосрочные стратегии
    - Низкая энтропия (<0.6): усиливаем serial + markov
    - Высокая энтропия (>0.88): доверяем statistical + ema
    - Momentum: всегда активен при достаточной истории
    """
    if not games:
        return []

    w = dict(weights)
    # Инициализируем дефолтные веса для всех стратегий
    for s in STRATEGY_FNS:
        if s not in w:
            w[s] = 1 / len(STRATEGY_FNS)

    # Пауза: гасим краткосрочные паттерны
    if pause_detected:
        for s in ("serial", "markov", "zigzag", "momentum"):
            w[s] = w.get(s, 0) * 0.12
        w["ema"] = w.get("ema", 0) * 0.4

    # Энтропия-адаптация
    H       = shannon_entropy(games, window=min(25, len(games)))
    max_H   = math.log2(11)
    h_ratio = H / max_H

    if h_ratio < 0.60 and not pause_detected:
        # Паттерн предсказуем → усиливаем серийные
        w["serial"]  = w.get("serial",  0) * 1.6
        w["markov"]  = w.get("markov",  0) * 1.6
        w["momentum"] = w.get("momentum", 0) * 1.3
    elif h_ratio > 0.88:
        # Хаос → доверяем статистике
        w["serial"]      = w.get("serial",      0) * 0.45
        w["markov"]      = w.get("markov",       0) * 0.45
        w["zigzag"]      = w.get("zigzag",       0) * 0.55
        w["statistical"] = w.get("statistical",  0) * 1.5
        w["ema"]         = w.get("ema",          0) * 1.35

    # Нормализуем веса
    w_total = sum(w.values()) or 1
    norm_w  = {k: v / w_total for k, v in w.items()}

    # Вычисляем скоры каждой стратегии
    strat_scores: dict[str, ScoreMap] = {}
    for key, fn in STRATEGY_FNS.items():
        try:
            strat_scores[key] = fn(games)
        except Exception:
            strat_scores[key] = _uniform()

    # Взвешенная сумма
    combined: dict[int, float] = {}
    for num in NUMS:
        total_score = 0.0
        for key, smap in strat_scores.items():
            total_score += norm_w.get(key, 0.0) * smap.get(num, 0)
        combined[num] = total_score

    # Agreement boost: только при консенсусе 5+ из 7 стратегий
    n_strategies = len(STRATEGY_FNS)
    for num in NUMS:
        supporters = sum(
            1 for key, smap in strat_scores.items()
            if smap.get(num, 0) > UNIFORM * 1.4
        )
        if supporters >= n_strategies - 1:   # 6 из 7
            combined[num] *= 1.20
        elif supporters >= n_strategies - 2: # 5 из 7
            combined[num] *= 1.08

    # Softmax с temperature=0.7 (умереннее, чем 0.4 в v5)
    softmaxed = _softmax(combined, temperature=0.7)
    ranked    = sorted(softmaxed.items(), key=lambda x: x[1], reverse=True)

    results = []
    for num, score in ranked[:top_n]:
        lift = score / UNIFORM

        # Честная калибровка: вероятность "выше случайного" в процентах
        # lift=1.0 → 0% преимущества; lift=2.0 → 50% преимущества (раз в 2 лучше)
        # Максимально возможный lift при 11 числах ≈ 11 (100% уверенность)
        advantage_pct = round((lift - 1.0) / (11.0 - 1.0) * 100, 1)
        advantage_pct = max(0.0, min(advantage_pct, 99.0))

        # Рейтинг поддержки
        supporters = sum(
            1 for key, smap in strat_scores.items()
            if smap.get(num, 0) > UNIFORM * 1.4
        )

        explanation = _build_explanation(num, games, strat_scores, norm_w, h_ratio, H)

        results.append({
            "number":          num,
            "score":           score,
            "probability":     round(score * 100, 1),   # реальная вероятность %
            "advantage":       advantage_pct,            # преимущество над случайным
            "lift":            round(lift, 2),
            "strategy_scores": {k: round(v.get(num, 0) * 100, 1) for k, v in strat_scores.items()},
            "explanation":     explanation,
            "entropy":         round(H, 2),
            "supporters":      supporters,
            "h_ratio":         round(h_ratio, 2),
        })

    return results


def _build_explanation(
    num:          int,
    games:        list[dict],
    strat_scores: dict[str, ScoreMap],
    weights:      dict[str, float],
    h_ratio:      float,
    H:            float,
) -> str:
    parts = []
    freq  = frequency(games)
    total = len(games) or 1
    pct   = freq[num] / total * 100

    sorted_freq = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    rank = next((i for i, (n, _) in enumerate(sorted_freq) if n == num), 10)

    if rank == 0:
        parts.append(f"🔥 лидер ({pct:.0f}%)")
    elif rank < 3:
        parts.append(f"🔥 горячее ({pct:.0f}%)")
    elif rank >= 9:
        parts.append(f"❄️ редкое ({pct:.0f}%)")
    else:
        parts.append(f"частота {pct:.0f}%")

    m_score = strat_scores["markov"].get(num, 0)
    if m_score > UNIFORM * 1.5:
        parts.append(f"цепь Маркова ↑")

    sr_score = strat_scores["serial"].get(num, 0)
    if sr_score > UNIFORM * 1.5:
        parts.append(f"паттерн серии")

    ema_s = strat_scores["ema"].get(num, 0)
    mom_s = strat_scores["momentum"].get(num, 0)
    if ema_s > UNIFORM * 1.4 and mom_s > UNIFORM * 1.3:
        parts.append("тренд ↑↑")
    elif ema_s > UNIFORM * 1.4:
        parts.append("EMA-тренд ↑")
    elif mom_s > UNIFORM * 1.4:
        parts.append("моментум ↑")

    zz_s = strat_scores["zigzag"].get(num, 0)
    if zz_s > UNIFORM * 1.5:
        parts.append("зигзаг")

    ta = time_analysis(games)
    if num in ta.get("hour_top", []) and ta["hour_sample"] >= 10:
        parts.append(f"актив в {ta['current_hour']}ч")

    if h_ratio < 0.55:
        parts.append(f"низкая энтропия")

    return " · ".join(parts) if parts else "нет явного паттерна"


# ── Рекомендация ставки ───────────────────────────────────────────────────────

def bet_recommendation(top_pred: dict, weights: dict) -> str:
    """
    Рекомендация основана на реальном преимуществе над случайным.
    Убраны завышенные пороги v5.
    """
    adv        = top_pred.get("advantage", 0)
    lift       = top_pred.get("lift", 1.0)
    supporters = top_pred.get("supporters", 0)
    prob       = top_pred.get("probability", 9.1)

    n_strats   = len(STRATEGY_FNS)
    consensus  = supporters >= n_strats - 2   # 5 из 7

    if adv >= 30 and consensus and lift >= 1.8:
        return "🟢 Уверенная — сильный консенсус стратегий"
    elif adv >= 18 and (supporters >= 4 or lift >= 1.4):
        return "🟡 Осторожная — умеренный сигнал"
    elif adv >= 8:
        return "🟠 Слабая — сигнал минимален, минимальная ставка"
    else:
        return "🔴 Пропустить — стратегии не согласованы"
