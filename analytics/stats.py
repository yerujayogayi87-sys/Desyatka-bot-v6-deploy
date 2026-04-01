"""
analytics/stats.py — математическое ядро аналитики.

Улучшения v5:
  - EMA (экспоненциальное скользящее среднее) для тренда
  - Энтропия Шеннона — мера случайности последовательности
  - Corrected gap score — нормализованный gap с учётом дисперсии
  - Zigzag detector — паттерн чередования high/low
  - Cluster analysis — группировка чисел по диапазонам с трендом
  - Improved alerts — умные пороги вместо фиксированных
"""

from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional
import math


# ── Базовая статистика ────────────────────────────────────────────────────────

def frequency(games: list[dict]) -> dict[int, int]:
    counts = {i: 0 for i in range(11)}
    for g in games:
        counts[g["result"]] += 1
    return counts


def hot_cold(games: list[dict], top_n: int = 3) -> tuple[list[int], list[int]]:
    freq = frequency(games)
    ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [n for n, _ in ranked[:top_n]], [n for n, _ in ranked[-top_n:]]


def gap_analysis(games: list[dict]) -> dict[int, int]:
    """Сколько игр прошло с последнего выпадения каждого числа."""
    results = [g["result"] for g in games]
    gaps = {}
    for num in range(11):
        try:
            gaps[num] = results.index(num)
        except ValueError:
            gaps[num] = len(results) + 1
    return gaps


def deviation_scores(games: list[dict]) -> dict[int, float]:
    freq = frequency(games)
    total = len(games) or 1
    expected = total / 11
    return {n: (c - expected) / (expected or 1) for n, c in freq.items()}


def even_odd_streak(games: list[dict]) -> dict:
    if not games:
        return {}
    results = [g["result"] for g in games]
    total = len(results)
    even_count = sum(1 for r in results if r % 2 == 0)
    odd_count = total - even_count

    current_parity = results[0] % 2
    streak = 1
    for r in results[1:]:
        if r % 2 == current_parity:
            streak += 1
        else:
            break

    return {
        "even": even_count,
        "odd": odd_count,
        "current_parity": "чётное" if current_parity == 0 else "нечётное",
        "current_streak": streak,
        "even_pct": even_count / total * 100 if total else 0,
        "odd_pct":  odd_count  / total * 100 if total else 0,
    }


def range_stats(games: list[dict]) -> dict:
    results = [g["result"] for g in games]
    total = len(results) or 1
    low  = sum(1 for r in results if r <= 3)
    mid  = sum(1 for r in results if 4 <= r <= 7)
    high = sum(1 for r in results if r >= 8)
    return {
        "low":  (low,  low  / total * 100),
        "mid":  (mid,  mid  / total * 100),
        "high": (high, high / total * 100),
    }


def find_runs(games: list[dict]) -> dict:
    if not games:
        return {}
    results = [g["result"] for g in games]

    cur_val, cur_len = results[0], 1
    for r in results[1:]:
        if r == cur_val:
            cur_len += 1
        else:
            break

    max_len, max_val = 1, results[0]
    tmp_len = 1
    for i in range(1, len(results)):
        if results[i] == results[i - 1]:
            tmp_len += 1
            if tmp_len > max_len:
                max_len, max_val = tmp_len, results[i]
        else:
            tmp_len = 1

    return {
        "current_value":  cur_val,
        "current_length": cur_len,
        "max_length":     max_len,
        "max_value":      max_val,
    }


def alternation_score(games: list[dict], window: int = 10) -> float:
    results = [g["result"] for g in games[:window]]
    if len(results) < 2:
        return 0.5
    changes = sum(1 for i in range(1, len(results)) if results[i] != results[i - 1])
    return changes / (len(results) - 1)


# ── EMA — экспоненциальное скользящее среднее частоты ───────────────────────

def ema_scores(games: list[dict], alpha: float = 0.15) -> dict[int, float]:
    """
    Вычисляет EMA-вес каждого числа: свежие игры весят больше.
    alpha=0.15 → половина веса на ~4.3 последних играх.
    Возвращает нормализованный словарь [0..1].
    """
    scores = {i: 0.0 for i in range(11)}
    # games[0] — последняя, идём от старых к новым
    for g in reversed(games):
        n = g["result"]
        for k in scores:
            scores[k] = scores[k] * (1 - alpha)
        scores[n] += alpha

    total = sum(scores.values()) or 1
    return {k: v / total for k, v in scores.items()}


# ── Энтропия Шеннона ─────────────────────────────────────────────────────────

def shannon_entropy(games: list[dict], window: int = 30) -> float:
    """
    Энтропия последних `window` результатов (0 = детерминировано, log2(11)≈3.46 = равномерно).
    Низкая энтропия → паттерн более предсказуем.
    """
    results = [g["result"] for g in games[:window]]
    if not results:
        return math.log2(11)
    cnt = Counter(results)
    total = len(results)
    return -sum((c / total) * math.log2(c / total) for c in cnt.values() if c > 0)


# ── Zigzag — паттерн чередования высоких/низких ──────────────────────────────

def zigzag_score(games: list[dict], window: int = 10) -> dict[int, float]:
    """
    Определяет, идёт ли последовательность зигзагом (high↔low).
    Если да — повышает вес противоположного диапазона следующему числу.
    Возвращает нормализованный score по числам.
    """
    results = [g["result"] for g in games[:window]]
    if len(results) < 4:
        return {i: 1 / 11 for i in range(11)}

    # Классифицируем: L=0-3, M=4-7, H=8-10
    def zone(n):
        return "L" if n <= 3 else ("H" if n >= 8 else "M")

    zones = [zone(r) for r in results]
    # Считаем зигзаги L→H или H→L пропуская M
    non_mid = [z for z in zones if z != "M"]
    zz_count = sum(1 for i in range(1, len(non_mid)) if non_mid[i] != non_mid[i - 1])
    zz_rate = zz_count / max(len(non_mid) - 1, 1)

    scores = {i: 1.0 for i in range(11)}
    if zz_rate >= 0.7 and non_mid:
        last_zone = non_mid[0]  # самый свежий
        if last_zone == "L":
            for n in range(8, 11):
                scores[n] = 2.5
        elif last_zone == "H":
            for n in range(0, 4):
                scores[n] = 2.5

    total = sum(scores.values())
    return {k: v / total for k, v in scores.items()}


# ── Cluster trend — тренд по диапазонам ──────────────────────────────────────

def cluster_trend(games: list[dict], short: int = 10, long: int = 30) -> dict[str, float]:
    """
    Сравнивает частоту диапазонов в короткий и длинный периоды.
    Возвращает trend_ratio для каждого диапазона (>1 = растёт, <1 = падает).
    """
    def zone_freq(subset):
        total = len(subset) or 1
        return {
            "L": sum(1 for g in subset if g["result"] <= 3) / total,
            "M": sum(1 for g in subset if 4 <= g["result"] <= 7) / total,
            "H": sum(1 for g in subset if g["result"] >= 8) / total,
        }

    s = zone_freq(games[:short])
    l = zone_freq(games[:long])

    return {
        z: s[z] / (l[z] + 1e-6) for z in ("L", "M", "H")
    }


# ── Марковские цепи ───────────────────────────────────────────────────────────

def build_markov(games: list[dict], order: int = 2) -> dict:
    results = [g["result"] for g in reversed(games)]
    counts: dict = defaultdict(Counter)
    for i in range(len(results) - order):
        ctx = tuple(results[i: i + order])
        counts[ctx][results[i + order]] += 1
    probs = {}
    for ctx, counter in counts.items():
        total = sum(counter.values())
        probs[ctx] = {n: c / total for n, c in counter.items()}
    return probs


def markov_predict(games: list[dict], order: int = 2) -> dict[int, float]:
    if len(games) <= order:
        return {}
    results_desc = [g["result"] for g in games]
    matrix = build_markov(games, order)
    context = tuple(reversed(results_desc[:order]))
    if context in matrix:
        return matrix[context]
    # Fallback order-1
    if order > 1:
        m1 = build_markov(games, 1)
        ctx1 = (results_desc[0],)
        if ctx1 in m1:
            return m1[ctx1]
    return {}


def markov_predict_order3(games: list[dict]) -> dict[int, float]:
    """Цепь 3-го порядка для длинных историй."""
    if len(games) < 15:
        return {}
    return markov_predict(games, order=3) or markov_predict(games, order=2)


# ── Временной анализ ──────────────────────────────────────────────────────────

def time_analysis(games: list[dict]) -> dict:
    now = datetime.now()
    current_hour = now.hour
    current_dow  = now.weekday()

    hour_results = [
        g["result"] for g in games
        if g.get("hour_of_day") is not None and abs(g["hour_of_day"] - current_hour) <= 2
    ]
    dow_results = [g["result"] for g in games if g.get("day_of_week") == current_dow]

    def top_num(lst, n=3):
        if not lst:
            return []
        return [num for num, _ in Counter(lst).most_common(n)]

    return {
        "hour_sample": len(hour_results),
        "hour_top":    top_num(hour_results),
        "dow_sample":  len(dow_results),
        "dow_top":     top_num(dow_results),
        "current_hour": current_hour,
        "current_dow": ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"][current_dow],
    }


# ── Пауза между сессиями ─────────────────────────────────────────────────────

def detect_pause(games: list[dict], threshold_minutes: int = 60) -> Optional[float]:
    if len(games) < 2:
        return None
    try:
        t_last  = datetime.fromisoformat(games[0]["added_at"])
        t_prev  = datetime.fromisoformat(games[1]["added_at"])
        diff_min = (t_last - t_prev).total_seconds() / 60
        if diff_min > threshold_minutes:
            return round(diff_min / 60, 1)
    except Exception:
        pass
    return None


# ── Умные предупреждения ──────────────────────────────────────────────────────

def get_alerts(games: list[dict]) -> list[str]:
    alerts = []
    if len(games) < 5:
        return alerts

    eo   = even_odd_streak(games)
    runs = find_runs(games)
    gaps = gap_analysis(games)
    total = len(games)

    # Адаптивный порог для серии чёт/нечет
    streak = eo.get("current_streak", 0)
    if streak >= 5:
        opposite = "нечётное" if eo["current_parity"] == "чётное" else "чётное"
        alerts.append(
            f"⚠️ {streak} раз подряд: {eo['current_parity']}! "
            f"Вероятность отката на {opposite} растёт."
        )

    # Серия одного числа
    cur_len = runs.get("current_length", 0)
    if cur_len >= 3:
        alerts.append(
            f"⚠️ Число {runs['current_value']} выпало {cur_len} раза подряд!"
        )

    # Перекос чёт/нечет
    even_pct = eo.get("even_pct", 50)
    if abs(even_pct - 50) > 15:
        dominant = "чётные" if even_pct > 50 else "нечётные"
        pct_val  = max(even_pct, 100 - even_pct)
        alerts.append(f"📊 Перекос: {dominant} {pct_val:.0f}% от всех игр.")

    # Просроченные числа — адаптивный порог (15% от базы или мин. 10)
    overdue_thresh = max(10, int(total * 0.15))
    overdue = sorted([n for n, g in gaps.items() if g >= overdue_thresh])
    if overdue:
        alerts.append(f"⏳ Не выпадали {overdue_thresh}+ игр: {', '.join(str(n) for n in overdue)}")

    # Зигзаг паттерн
    zz = zigzag_score(games, window=8)
    max_zz = max(zz.values())
    if max_zz > 0.25:
        top_zz = max(zz, key=zz.get)
        alerts.append(f"🔀 Зигзаг-паттерн: повышена вероятность числа {top_zz}")

    # Низкая энтропия — предсказуемая серия
    H = shannon_entropy(games, window=20)
    max_H = math.log2(11)
    if H < max_H * 0.55 and total >= 15:
        alerts.append(f"🎯 Низкая энтропия ({H:.2f}/{max_H:.2f}) — паттерн активен!")

    return alerts
