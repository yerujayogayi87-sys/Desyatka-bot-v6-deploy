"""keyboards/inline_keyboards.py — все inline-клавиатуры бота v6."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def kb_main_menu(uid: int = 0) -> InlineKeyboardMarkup:
    """Главное меню. Если uid — администратор, добавляется кнопка панели."""
    from database import is_admin
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить игру",   callback_data="add_game"),
        InlineKeyboardButton(text="🎯 Прогноз числа",   callback_data="get_predict"),
    )
    builder.row(
        InlineKeyboardButton(text="⚖️ Чёт / Нечет",     callback_data="predict_even_odd"),
        InlineKeyboardButton(text="🤖 Автопилот",        callback_data="autopilot"),
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика",       callback_data="stats"),
        InlineKeyboardButton(text="📜 История игр",      callback_data="history"),
    )
    builder.row(
        InlineKeyboardButton(text="🔥 Горячая полоса",  callback_data="hot_streak"),
        InlineKeyboardButton(text="⚙️ Настройки",        callback_data="settings"),
    )
    if uid and is_admin(uid):
        builder.row(
            InlineKeyboardButton(text="🔐 Панель администратора", callback_data="admin_panel"),
        )
    return builder.as_markup()


def kb_after_add() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить ещё",      callback_data="add_game"),
        InlineKeyboardButton(text="🔄 Обновить прогноз",  callback_data="get_predict"),
    )
    builder.row(
        InlineKeyboardButton(text="📝 Подробный разбор", callback_data="predict_detail"),
        InlineKeyboardButton(text="🤖 Автопилот",         callback_data="autopilot"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"),
    )
    return builder.as_markup()


def kb_predict() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить игру",    callback_data="add_game"),
        InlineKeyboardButton(text="📝 Подробнее",        callback_data="predict_detail"),
    )
    builder.row(
        InlineKeyboardButton(text="🌡️ Тепловая карта",   callback_data="heatmap"),
        InlineKeyboardButton(text="🤖 Автопилот",        callback_data="autopilot"),
    )
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню",     callback_data="main_menu"),
    )
    return builder.as_markup()


def kb_pause_detected() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🆕 Новая сессия",       callback_data="new_session"),
        InlineKeyboardButton(text="▶️ Продолжить как есть", callback_data="continue_session"),
    )
    return builder.as_markup()


def kb_number_input() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    row1 = [InlineKeyboardButton(text=str(i), callback_data=f"num_{i}") for i in range(6)]
    row2 = [InlineKeyboardButton(text=str(i), callback_data=f"num_{i}") for i in range(6, 11)]
    builder.row(*row1)
    builder.row(*row2)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_add"))
    return builder.as_markup()


def kb_history(games: list[dict], page: int = 0, per_page: int = 10) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    start = page * per_page
    end   = start + per_page
    for g in games[start:end]:
        dt  = (g.get("added_at") or "")[:16]
        gn  = f" · #{g['game_number']}" if g.get("game_number") else ""
        builder.row(
            InlineKeyboardButton(
                text=f"#{g['id']}{gn} · {g['result']} · {dt}",
                callback_data=f"game_detail_{g['id']}",
            )
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"hist_page_{page-1}"))
    if end < len(games):
        nav.append(InlineKeyboardButton(text="▶️ Далее", callback_data=f"hist_page_{page+1}"))
    if nav:
        builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def kb_game_detail(game_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_game_{game_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="◀️ К истории",    callback_data="history"),
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"),
    )
    return builder.as_markup()


def kb_stats() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📊 График частот",      callback_data="stats_chart"),
        InlineKeyboardButton(text="🌡️ Тепловая карта",     callback_data="heatmap"),
    )
    builder.row(
        InlineKeyboardButton(text="⚖️ График чёт/нечет",   callback_data="even_odd_chart"),
        InlineKeyboardButton(text="⚖️ Прогноз чёт/нечет",  callback_data="predict_even_odd"),
    )
    builder.row(
        InlineKeyboardButton(text="📈 Бэктестинг",         callback_data="compare"),
        InlineKeyboardButton(text="🗂 Сессии",              callback_data="sessions"),
    )
    builder.row(
        InlineKeyboardButton(text="🔥 Горячая полоса",     callback_data="hot_streak"),
        InlineKeyboardButton(text="🤖 Автопилот",          callback_data="autopilot"),
    )
    builder.row(
        InlineKeyboardButton(text="🔬 Калибровка",         callback_data="calibration"),
        InlineKeyboardButton(text="⚠️ Алерты",             callback_data="alerts"),
        InlineKeyboardButton(text="📥 Экспорт",            callback_data="export"),
    )
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def kb_settings(settings: dict) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    def flag(v): return "✅" if v else "❌"

    builder.row(
        InlineKeyboardButton(
            text=f"{flag(settings.get('use_statistical',1))} Статистика",
            callback_data="toggle_statistical",
        ),
        InlineKeyboardButton(
            text=f"{flag(settings.get('use_ema',1))} EMA",
            callback_data="toggle_ema",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{flag(settings.get('use_serial',1))} Серии",
            callback_data="toggle_serial",
        ),
        InlineKeyboardButton(
            text=f"{flag(settings.get('use_markov',1))} Марков",
            callback_data="toggle_markov",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{flag(settings.get('use_zigzag',1))} Зигзаг",
            callback_data="toggle_zigzag",
        ),
        InlineKeyboardButton(
            text=f"{flag(settings.get('use_temporal',1))} Время",
            callback_data="toggle_temporal",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=f"{flag(settings.get('use_recency',1))} Последние",
            callback_data="toggle_recency",
        ),
        InlineKeyboardButton(
            text="⏱ Порог паузы",
            callback_data="set_pause_threshold",
        ),
    )
    builder.row(
        InlineKeyboardButton(text="🗑 Очистить всё",  callback_data="clear_confirm"),
        InlineKeyboardButton(text="📥 Экспорт",       callback_data="export"),
    )
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def kb_clear_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, очистить", callback_data="clear_do"),
        InlineKeyboardButton(text="❌ Отмена",        callback_data="settings"),
    )
    return builder.as_markup()


def kb_back_main() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"))
    return builder.as_markup()


def kb_pause_threshold() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for m in [30, 60, 120, 240]:
        builder.button(text=f"{m} мин", callback_data=f"pause_{m}")
    builder.adjust(4)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="settings"))
    return builder.as_markup()
