"""
analytics/charts.py — генерация графиков через matplotlib.
"""

import matplotlib
matplotlib.use("Agg")
import io
import math
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from collections import Counter


NUM_COLORS = {
    0: "#9e9e9e", 1: "#f44336", 2: "#2196f3", 3: "#4caf50",
    4: "#9c27b0", 5: "#00bcd4", 6: "#ffeb3b", 7: "#ff9800",
    8: "#1565c0", 9: "#795548", 10: "#212121",
}
BG_DARK  = "#1a1a2e"
BG_PANEL = "#16213e"
TEXT_CLR = "#ffffffcc"
GRID_CLR = "#ffffff18"


def _apply_dark(fig, *axes):
    fig.patch.set_facecolor(BG_DARK)
    for ax in axes:
        ax.set_facecolor(BG_PANEL)
        ax.spines[:].set_color("#ffffff22")
        ax.tick_params(colors=TEXT_CLR)
        ax.xaxis.label.set_color(TEXT_CLR)
        ax.yaxis.label.set_color(TEXT_CLR)
        ax.grid(color=GRID_CLR, linewidth=0.6, zorder=0)


def _to_buf(fig) -> io.BytesIO:
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def make_frequency_chart(games: list[dict]) -> io.BytesIO:
    results = [g["result"] for g in games]
    total   = len(results) or 1
    freq    = {i: results.count(i) for i in range(11)}
    expected = total / 11

    fig, ax = plt.subplots(figsize=(10, 5))
    _apply_dark(fig, ax)

    bars = ax.bar(
        range(11), [freq[n] for n in range(11)],
        color=[NUM_COLORS[n] for n in range(11)],
        edgecolor="#ffffff22", linewidth=0.8, zorder=3
    )
    ax.axhline(expected, color="#ff6b6b", linestyle="--", linewidth=1.8,
               label=f"Ожидаемое ({expected:.1f}×)", zorder=4)

    for bar, n in zip(bars, range(11)):
        pct = freq[n] / total * 100
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                f"{pct:.0f}%", ha="center", va="bottom",
                color=TEXT_CLR, fontsize=8, fontweight="bold")

    ax.set_xticks(range(11))
    ax.set_xticklabels([str(n) for n in range(11)], color=TEXT_CLR, fontsize=11)
    ax.set_ylabel("Выпадений", color=TEXT_CLR, fontsize=9)
    ax.set_title(f"Частота чисел · {total} игр", color="white",
                 fontsize=13, fontweight="bold", pad=12)
    ax.legend(facecolor=BG_DARK, edgecolor="#ffffff22", labelcolor="white", fontsize=9)
    plt.tight_layout()
    return _to_buf(fig)


def make_even_odd_chart(games: list[dict]) -> io.BytesIO:
    results = [g["result"] for g in games[:60]]
    results.reverse()
    xs = list(range(1, len(results) + 1))

    ec = oc = 0
    even_cum, odd_cum = [], []
    for r in results:
        if r % 2 == 0: ec += 1
        else:          oc += 1
        even_cum.append(ec)
        odd_cum.append(oc)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6))
    _apply_dark(fig, ax1, ax2)

    for x, r in zip(xs, results):
        color = "#4fc3f7" if r % 2 == 0 else "#ff8a65"
        ax1.scatter(x, r, color=color, s=50, zorder=3)

    ax1.set_ylabel("Число", color=TEXT_CLR, fontsize=9)
    ax1.set_title("Чёт / Нечет — последние игры", color="white",
                  fontsize=11, fontweight="bold")
    p1 = mpatches.Patch(color="#4fc3f7", label="Чётные")
    p2 = mpatches.Patch(color="#ff8a65", label="Нечётные")
    ax1.legend(handles=[p1, p2], facecolor=BG_DARK, edgecolor="#ffffff22",
               labelcolor="white", fontsize=8)

    ax2.plot(xs, even_cum, color="#4fc3f7", label="Чётные (накоп.)", linewidth=2)
    ax2.plot(xs, odd_cum,  color="#ff8a65", label="Нечётные (накоп.)", linewidth=2)
    ax2.set_ylabel("Накоплено", color=TEXT_CLR, fontsize=9)
    ax2.set_xlabel("Игры", color=TEXT_CLR, fontsize=9)
    ax2.legend(facecolor=BG_DARK, edgecolor="#ffffff22", labelcolor="white", fontsize=8)

    plt.tight_layout()
    return _to_buf(fig)


def make_strategy_radar(strategy_scores: dict[str, float], number: int) -> io.BytesIO:
    """
    Radar/spider chart по вкладу каждой стратегии для числа.
    """
    labels = list(strategy_scores.keys())
    values = [strategy_scores[k] for k in labels]
    N = len(labels)
    angles = [n / N * 2 * math.pi for n in range(N)]
    angles += angles[:1]
    values_plot = values + values[:1]

    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw=dict(polar=True))
    fig.patch.set_facecolor(BG_DARK)
    ax.set_facecolor(BG_PANEL)

    ax.plot(angles, values_plot, color="#4fc3f7", linewidth=2)
    ax.fill(angles, values_plot, color="#4fc3f7", alpha=0.25)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels, color=TEXT_CLR, size=10)
    ax.set_yticklabels([])
    ax.spines["polar"].set_color("#ffffff22")
    ax.grid(color=GRID_CLR)

    ax.set_title(f"Стратегии для числа {number}", color="white",
                 fontsize=13, fontweight="bold", pad=20)
    plt.tight_layout()
    return _to_buf(fig)


def make_history_heatmap(games: list[dict]) -> io.BytesIO:
    """
    Тепловая карта: строки = числа 0–10, столбцы = последние 40 игр.
    Зелёная клетка = выпало, тёмная = нет.
    """
    n_games = min(40, len(games))
    subset  = list(reversed(games[:n_games]))

    matrix = [[0] * n_games for _ in range(11)]
    for col, g in enumerate(subset):
        matrix[g["result"]][col] = 1

    fig, ax = plt.subplots(figsize=(12, 4))
    _apply_dark(fig, ax)

    data = [[matrix[row][col] for col in range(n_games)] for row in range(11)]
    ax.imshow(data, aspect="auto", cmap="Greens", vmin=0, vmax=1,
              interpolation="nearest")

    ax.set_yticks(range(11))
    ax.set_yticklabels([str(n) for n in range(11)], color=TEXT_CLR, fontsize=9)
    ax.set_xticks(range(0, n_games, 5))
    ax.set_xticklabels([str(i + 1) for i in range(0, n_games, 5)],
                       color=TEXT_CLR, fontsize=8)
    ax.set_xlabel("Игра (→ самая свежая справа)", color=TEXT_CLR, fontsize=9)
    ax.set_ylabel("Число", color=TEXT_CLR, fontsize=9)
    ax.set_title(f"Тепловая карта последних {n_games} игр",
                 color="white", fontsize=12, fontweight="bold")
    plt.tight_layout()
    return _to_buf(fig)
