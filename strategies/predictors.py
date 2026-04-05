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


def _recency_anchor(games: list[dict], window: int = 24, half_life: int = 10) -> ScoreMap:
    """
    Стабилизирующее распределение по последним играм.
    Используется как небольшой якорь, чтобы снизить шум переобучения ансамбля.
    """
    if not games:
        return _uniform()

    subset = games[: min(window, len(games))]
    scores = {i: 0.0 for i in NUMS}
    for idx, game in enumerate(subset):
        decay = math.exp(-idx * math.log(2) / max(half_life, 1))
        scores[game["result"]] += decay
    return _normalize(scores)


def _calibrate_distribution(raw_scores: ScoreMap, games: list[dict], h_ratio: float) -> ScoreMap:
    """
    Калибровка итогового распределения: мягко тянем к равномерному,
    когда данных мало или последовательность близка к случайной.
    """
    n = len(games)
    if n <= 0:
        return _uniform()

    sample_factor = min(n / 40.0, 1.0)
    entropy_factor = max(0.0, min((h_ratio - 0.70) / 0.25, 1.0))

    # Чем выше энтропия и меньше выборка, тем сильнее shrink к uniform.
    shrink = 0.02 + 0.18 * (1.0 - sample_factor) + 0.12 * entropy_factor
    shrink = min(max(shrink, 0.02), 0.30)

    calibrated = {
        num: raw_scores.get(num, 0.0) * (1.0 - shrink) + UNIFORM * shrink
        for num in NUMS
    }
    return _normalize(calibrated)


WINDOW_BLEND = {
    "statistical": (0.25, 0.35, 0.40),
    "ema":         (0.50, 0.30, 0.20),
    "serial":      (0.55, 0.30, 0.15),
    "markov":      (0.50, 0.30, 0.20),
    "zigzag":      (0.60, 0.25, 0.15),
    "temporal":    (0.20, 0.30, 0.50),
    "momentum":    (0.55, 0.30, 0.15),
}


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

DEFAULT_STRATEGY_PRIORS = {
    "statistical": 0.06,
    "ema":         0.40,
    "serial":      0.07,
    "markov":      0.23,
    "zigzag":      0.10,
    "temporal":    0.05,
    "momentum":    0.15,
}


def _safe_strategy_scores(games: list[dict]) -> dict[str, ScoreMap]:
    scores: dict[str, ScoreMap] = {}
    for key, fn in STRATEGY_FNS.items():
        try:
            scores[key] = _window_blended_strategy_score(key, games)
        except Exception:
            scores[key] = _uniform()
    return scores


def _window_blended_strategy_score(strategy: str, games: list[dict]) -> ScoreMap:
    fn = STRATEGY_FNS[strategy]
    n = len(games)
    if n < 12:
        return fn(games)

    short_n = min(18, n)
    mid_n = min(40, n)
    subsets = (games[:short_n], games[:mid_n], games)
    blend = WINDOW_BLEND[strategy]

    combined = {i: 0.0 for i in NUMS}
    total_w = 0.0
    for subset, weight in zip(subsets, blend):
        if not subset:
            continue
        smap = fn(subset)
        for num in NUMS:
            combined[num] += smap.get(num, 0.0) * weight
        total_w += weight

    if total_w <= 0:
        return fn(games)
    return _normalize(combined)


def strategy_top_predictions(games: list[dict]) -> dict[str, int]:
    """Лучшее число по каждой стратегии отдельно."""
    strat_scores = _safe_strategy_scores(games)
    tops: dict[str, int] = {}
    for key, smap in strat_scores.items():
        tops[key] = max(smap.items(), key=lambda item: item[1])[0]
    return tops


def _recent_strategy_weight_adjustment(games: list[dict], lookback: int = 28) -> dict[str, float]:
    """
    Оценивает, какие стратегии лучше работали на последних известных переходах.
    Это не заменяет веса из БД, а мягко корректирует их по свежей локальной истории.
    """
    if len(games) < 18:
        return {key: 1.0 for key in STRATEGY_FNS}

    n_checks = min(lookback, len(games) - 6)
    stats = {key: {"nll_sum": 0.0, "hits": 0, "total": 0} for key in STRATEGY_FNS}

    for offset in range(n_checks):
        hist = games[offset + 1 :]
        if len(hist) < 8:
            break
        actual = games[offset]["result"]
        strat_scores = _safe_strategy_scores(hist)
        for key, smap in strat_scores.items():
            p = max(smap.get(actual, UNIFORM), 1e-9)
            stats[key]["nll_sum"] += -math.log(p)
            top_num = max(smap.items(), key=lambda item: item[1])[0]
            stats[key]["hits"] += int(top_num == actual)
            stats[key]["total"] += 1

    adjusted = {}
    # NLL равномерного предсказания для 11 чисел.
    uniform_nll = math.log(11)
    max_total = max(n_checks, 1)
    for key, data in stats.items():
        total = data["total"]
        if total <= 0:
            adjusted[key] = 1.0
            continue
        avg_nll = data["nll_sum"] / max(total, 1)
        # skill_prob > 1 лучше uniform, <1 хуже uniform.
        skill_prob = math.exp(uniform_nll - avg_nll)
        hit_rate = (data["hits"] + 1) / (total + 11)
        skill_hit = hit_rate / (1 / 11)
        relative = skill_prob * 0.65 + skill_hit * 0.35

        confidence = min(total / max_total, 1.0)
        damp = 0.35 + 0.65 * confidence
        tuned = 1.0 + (relative - 1.0) * damp
        adjusted[key] = min(max(tuned, 0.80), 1.35)

    return adjusted


def prediction_strength(top_pred: dict) -> float:
    """
    Итоговая сила сигнала 0..100.
    Комбинирует advantage, консенсус и штраф за высокую энтропию.
    """
    supporters = int(top_pred.get("supporters", 0))
    h_ratio = float(top_pred.get("h_ratio", 1.0))
    lift = float(top_pred.get("lift", 1.0))
    spread = float(top_pred.get("spread", 0.0))

    if h_ratio <= 0.72:
        entropy_factor = 1.0
    elif h_ratio >= 0.94:
        entropy_factor = 0.45
    else:
        entropy_factor = 1.0 - (h_ratio - 0.72) / (0.94 - 0.72) * 0.55

    support_component = min(supporters / max(len(STRATEGY_FNS), 1), 1.0) * 24.0
    lift_component = min(max((lift - 1.0) / 1.4, 0.0), 1.0) * 34.0
    spread_component = min(max(spread, 0.0), 1.0) * 42.0
    strength = (support_component + lift_component + spread_component) * entropy_factor
    return round(max(0.0, min(strength, 99.0)), 1)


def _detect_regime(h_ratio: float, n_games: int, pause_detected: bool) -> str:
    if pause_detected:
        return "reset"
    if n_games < 12:
        return "cold_start"
    if h_ratio >= 0.90:
        return "noise"
    if h_ratio >= 0.78:
        return "mixed"
    return "structured"


def _regime_risk_factor(regime: str) -> float:
    return {
        "structured": 1.00,
        "mixed": 0.80,
        "noise": 0.58,
        "reset": 0.52,
        "cold_start": 0.62,
    }.get(regime, 0.75)


def _trade_gate(
    *,
    regime: str,
    games_count: int,
    effective_advantage: float,
    strength: float,
    supporters: int,
    probability_pct: float,
) -> tuple[bool, str, float]:
    """Решение: есть ли сделка, и какая доля ставки допустима."""
    if games_count < 12:
        return False, "мало данных", 0.0

    if regime in {"noise", "reset", "cold_start"}:
        min_edge, min_strength, min_support = 12.0, 18.0, 5
    elif regime == "mixed":
        min_edge, min_strength, min_support = 9.0, 14.0, 4
    else:
        min_edge, min_strength, min_support = 6.0, 10.0, 3

    if probability_pct < 10.2:
        return False, "близко к случайному", 0.0
    if effective_advantage < min_edge:
        return False, "недостаточный edge", 0.0
    if strength < min_strength:
        return False, "слабый сигнал", 0.0
    if supporters < min_support:
        return False, "нет консенсуса", 0.0

    edge_part = min(max((effective_advantage - min_edge) / 16.0, 0.0), 1.0)
    strength_part = min(max((strength - min_strength) / 26.0, 0.0), 1.0)
    support_part = min(max((supporters - min_support) / 3.0, 0.0), 1.0)

    stake_factor = 0.25 + 0.45 * edge_part + 0.20 * strength_part + 0.10 * support_part
    return True, "сигнал подтверждён", round(min(max(stake_factor, 0.20), 1.0), 2)


def _build_market_views(probs: ScoreMap) -> dict:
    even_prob = sum(v for n, v in probs.items() if n % 2 == 0)
    odd_prob = 1.0 - even_prob

    low_prob = sum(v for n, v in probs.items() if n <= 3)
    mid_prob = sum(v for n, v in probs.items() if 4 <= n <= 7)
    high_prob = sum(v for n, v in probs.items() if n >= 8)

    parity_side = "чет" if even_prob >= odd_prob else "нечет"
    parity_conf = max(even_prob, odd_prob)

    zone_map = {"низ": low_prob, "середина": mid_prob, "верх": high_prob}
    zone_side, zone_conf = max(zone_map.items(), key=lambda item: item[1])

    return {
        "parity_side": parity_side,
        "parity_prob": round(parity_conf * 100, 1),
        "zone_side": zone_side,
        "zone_prob": round(zone_conf * 100, 1),
    }


def _select_signal_mode(top_pred: dict) -> tuple[str, str]:
    regime = top_pred.get("regime", "mixed")
    tradable = bool(top_pred.get("tradable", False))
    eff_adv = float(top_pred.get("effective_advantage", 0.0))
    prob = float(top_pred.get("probability", 0.0))
    parity_prob = float(top_pred.get("parity_prob", 0.0))
    zone_prob = float(top_pred.get("zone_prob", 0.0))

    if tradable and eff_adv >= 12 and prob >= 14.0:
        return "number", "сильный edge по точному числу"
    if zone_prob >= 43.0 and regime in {"mixed", "noise", "cold_start"}:
        return "zone", "число шумит, зона стабильнее"
    if parity_prob >= 56.0:
        return "parity", "по чет/нечет сигнал стабильнее"
    if tradable:
        return "number", "точечный сигнал проходит фильтр"
    return "skip", "нет подтвержденного преимущества"


def evaluate_history_predictions(
    games: list[dict],
    sample_sizes: tuple[int | None, ...] = (50, 100, None),
) -> dict[str, dict]:
    """
    Честный walk-forward бэктест по уже набранной истории.
    Используется для показа пользователю реальной точности без зависимости от БД логов.
    """
    if len(games) < 8:
        return {}

    chronological = list(reversed(games))
    available = len(chronological) - 5
    if available <= 0:
        return {}

    summaries: dict[str, dict] = {}
    for sample in sample_sizes:
        checks = min(sample or available, available)
        if checks <= 0:
            continue

        start_idx = len(chronological) - checks
        hit1 = hit3 = parity = 0
        measured = 0

        for idx in range(start_idx, len(chronological)):
            if idx < 5:
                continue
            history = list(reversed(chronological[:idx]))
            actual = chronological[idx]["result"]
            preds = predict(history, dict(DEFAULT_STRATEGY_PRIORS), top_n=3)
            if not preds:
                continue
            measured += 1
            top_numbers = [p["number"] for p in preds[:3]]
            hit1 += int(preds[0]["number"] == actual)
            hit3 += int(actual in top_numbers)
            parity += int((preds[0]["number"] % 2) == (actual % 2))

        if measured <= 0:
            continue

        key = "all" if sample is None else str(sample)
        summaries[key] = {
            "label": "вся база" if sample is None else f"последние {sample}",
            "checks": measured,
            "top1": round(hit1 / measured * 100, 2),
            "top3": round(hit3 / measured * 100, 2),
            "parity": round(parity / measured * 100, 2),
            "hit1": hit1,
            "hit3": hit3,
            "parity_hits": parity,
        }

    return summaries


def _prepare_base_weights(weights: dict[str, float]) -> dict[str, float]:
    """Приводит входные веса к общему baseline стратегий."""
    w = dict(weights)
    baseline = 1 / len(STRATEGY_FNS)
    for key in STRATEGY_FNS:
        if key not in w:
            w[key] = DEFAULT_STRATEGY_PRIORS[key]
        else:
            w[key] *= DEFAULT_STRATEGY_PRIORS[key] / baseline
    return w


def _apply_regime_weight_rules(
    w: dict[str, float],
    *,
    h_ratio: float,
    pause_detected: bool,
) -> dict[str, float]:
    """Доменные правила усиления/ослабления стратегий по режиму серии."""
    tuned = dict(w)
    if pause_detected:
        for key in ("serial", "markov", "zigzag", "momentum"):
            tuned[key] = tuned.get(key, 0.0) * 0.12
        tuned["ema"] = tuned.get("ema", 0.0) * 0.4

    if h_ratio < 0.60 and not pause_detected:
        tuned["serial"] = tuned.get("serial", 0.0) * 1.75
        tuned["markov"] = tuned.get("markov", 0.0) * 1.75
        tuned["momentum"] = tuned.get("momentum", 0.0) * 1.3
    elif h_ratio > 0.88:
        tuned["serial"] = tuned.get("serial", 0.0) * 0.45
        tuned["markov"] = tuned.get("markov", 0.0) * 0.45
        tuned["zigzag"] = tuned.get("zigzag", 0.0) * 0.55
        tuned["statistical"] = tuned.get("statistical", 0.0) * 1.5
        tuned["ema"] = tuned.get("ema", 0.0) * 1.35

    return tuned


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values()) or 1.0
    return {k: v / total for k, v in weights.items()}


def _combine_scores(strat_scores: dict[str, ScoreMap], norm_w: dict[str, float]) -> ScoreMap:
    combined = {num: 0.0 for num in NUMS}
    for num in NUMS:
        combined[num] = sum(norm_w.get(key, 0.0) * smap.get(num, 0.0) for key, smap in strat_scores.items())
    return combined


def _apply_consensus_boost(combined: ScoreMap, strat_scores: dict[str, ScoreMap], norm_w: dict[str, float]) -> ScoreMap:
    boosted = dict(combined)
    n_strategies = len(STRATEGY_FNS)
    for num in NUMS:
        supporters = sum(1 for smap in strat_scores.values() if smap.get(num, 0.0) > UNIFORM * 1.4)
        weighted_support = sum(
            norm_w.get(key, 0.0)
            for key, smap in strat_scores.items()
            if smap.get(num, 0.0) > UNIFORM * 1.25
        )
        if supporters >= n_strategies - 1:
            boosted[num] *= 1.20
        elif supporters >= n_strategies - 2:
            boosted[num] *= 1.08
        if weighted_support >= 0.72:
            boosted[num] *= 1.06
    return boosted


def _blend_anchor(combined: ScoreMap, games: list[dict], h_ratio: float) -> ScoreMap:
    anchor = _recency_anchor(games, window=24, half_life=10)
    if h_ratio > 0.86:
        anchor_mix = 0.12
    elif h_ratio > 0.75:
        anchor_mix = 0.08
    else:
        anchor_mix = 0.04
    mixed = {
        num: combined.get(num, 0.0) * (1.0 - anchor_mix) + anchor[num] * anchor_mix
        for num in NUMS
    }
    return _normalize(mixed)


def _pick_temperature(h_ratio: float, n_games: int) -> float:
    if h_ratio < 0.66 and n_games >= 30:
        return 0.58
    if h_ratio < 0.82:
        return 0.64
    return 0.74


def _diversify_ranked(ranked: list[tuple[int, float]], top_n: int) -> list[tuple[int, float]]:
    """Диверсифицирует хвост top-N, сохраняя исходный top-1."""
    if not ranked or top_n <= 1:
        return ranked

    def _zone(num: int) -> str:
        return "L" if num <= 3 else ("H" if num >= 8 else "M")

    selected = [ranked[0]]
    pool = ranked[1: min(8, len(ranked))]
    while pool and len(selected) < top_n:
        best_idx = 0
        best_score = -1.0
        for idx, (num, score) in enumerate(pool):
            adjusted_score = score
            for chosen_num, _ in selected:
                if num % 2 == chosen_num % 2:
                    adjusted_score *= 0.94
                if _zone(num) == _zone(chosen_num):
                    adjusted_score *= 0.92
            if adjusted_score > best_score:
                best_score = adjusted_score
                best_idx = idx
        selected.append(pool.pop(best_idx))

    if len(selected) < top_n:
        for item in ranked:
            if item not in selected:
                selected.append(item)
            if len(selected) >= top_n:
                break

    return selected + [item for item in ranked if item not in selected]


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

    # Энтропия-адаптация
    H       = shannon_entropy(games, window=min(25, len(games)))
    max_H   = math.log2(11)
    h_ratio = H / max_H

    w = _prepare_base_weights(weights)
    w = _apply_regime_weight_rules(w, h_ratio=h_ratio, pause_detected=pause_detected)
    recent_adj = _recent_strategy_weight_adjustment(games)
    for key, factor in recent_adj.items():
        w[key] = w.get(key, 0.0) * factor
    norm_w = _normalize_weights(w)

    # Вычисляем скоры каждой стратегии
    strat_scores = _safe_strategy_scores(games)

    combined = _combine_scores(strat_scores, norm_w)
    combined = _apply_consensus_boost(combined, strat_scores, norm_w)
    combined = _blend_anchor(combined, games, h_ratio)
    combined = _calibrate_distribution(combined, games, h_ratio)

    temperature = _pick_temperature(h_ratio, len(games))

    softmaxed = _softmax(combined, temperature=temperature)
    ranked    = sorted(softmaxed.items(), key=lambda x: x[1], reverse=True)
    regime = _detect_regime(h_ratio, len(games), pause_detected)
    regime_factor = _regime_risk_factor(regime)
    market_views = _build_market_views(softmaxed)

    ranked = _diversify_ranked(ranked, top_n)
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    n_strategies = len(STRATEGY_FNS)

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
        score_gap = max(score - second_score, 0.0)
        spread = score_gap / max(score, 1e-9)

        support_ratio = supporters / max(n_strategies, 1)
        stability_factor = min(max(0.50 + 0.35 * support_ratio + 0.25 * spread, 0.40), 1.0)
        effective_advantage = round(advantage_pct * stability_factor * regime_factor, 1)

        strength_val = prediction_strength({
            "advantage": advantage_pct,
            "supporters": supporters,
            "h_ratio": h_ratio,
            "lift": lift,
            "spread": spread,
        })
        probability_pct = round(score * 100, 1)
        tradable, trade_reason, stake_factor = _trade_gate(
            regime=regime,
            games_count=len(games),
            effective_advantage=effective_advantage,
            strength=strength_val,
            supporters=supporters,
            probability_pct=probability_pct,
        )

        explanation = _build_explanation(num, games, strat_scores, norm_w, h_ratio, H)

        results.append({
            "number":          num,
            "score":           score,
            "probability":     probability_pct,   # реальная вероятность %
            "advantage":       advantage_pct,            # преимущество над случайным
            "effective_advantage": effective_advantage,  # риск-скорректированное преимущество
            "strength":        strength_val,
            "lift":            round(lift, 2),
            "score_gap":       round(score_gap, 4),
            "spread":          round(spread, 3),
            "strategy_scores": {k: round(v.get(num, 0) * 100, 1) for k, v in strat_scores.items()},
            "explanation":     explanation,
            "entropy":         round(H, 2),
            "supporters":      supporters,
            "h_ratio":         round(h_ratio, 2),
            "regime":          regime,
            "tradable":        tradable,
            "trade_reason":    trade_reason,
            "stake_factor":    stake_factor,
            "parity_side":     market_views["parity_side"],
            "parity_prob":     market_views["parity_prob"],
            "zone_side":       market_views["zone_side"],
            "zone_prob":       market_views["zone_prob"],
        })

    if results:
        mode, mode_reason = _select_signal_mode(results[0])
        for row in results:
            row["signal_mode"] = mode
            row["signal_mode_reason"] = mode_reason

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
    adv        = top_pred.get("effective_advantage", top_pred.get("advantage", 0))
    strength   = top_pred.get("strength", prediction_strength(top_pred))
    lift       = top_pred.get("lift", 1.0)
    supporters = top_pred.get("supporters", 0)
    regime     = top_pred.get("regime", "mixed")
    tradable   = bool(top_pred.get("tradable", False))
    stake_factor = float(top_pred.get("stake_factor", 0.0))

    n_strats   = len(STRATEGY_FNS)
    consensus  = supporters >= n_strats - 2   # 5 из 7

    if not tradable:
        reason = top_pred.get("trade_reason", "сигнал не прошёл фильтр")
        return f"🔴 Пропустить — {reason}"

    if strength >= 26 and adv >= 22 and consensus and lift >= 1.65 and stake_factor >= 0.70:
        return "🟢 Уверенная — сильный консенсус стратегий"
    elif strength >= 15 and adv >= 12 and (supporters >= 4 or lift >= 1.35) and stake_factor >= 0.45:
        return "🟡 Рабочая — умеренный сигнал"
    elif strength >= 10 and adv >= 7:
        return "🟠 Ограниченная — только минимальная доля"
    else:
        return "🔴 Пропустить — стратегии не согласованы"
