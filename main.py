#!/usr/bin/env python3
"""
main.py — точка входа Desyatka Bot v6.

Новое:
  - AccessMiddleware: фильтрует все апдейты кроме /start и ввода токена
  - admin_router подключён первым (приоритет)
  - ADMIN_ID из .env — обязателен для production
"""

import asyncio
import logging
import os
import math
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)


# ── Middleware: проверка доступа на каждый апдейт ──────────────────────────────

from typing import Callable, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update, Message


class AccessMiddleware(BaseMiddleware):
    """
    Пропускает апдейт только если пользователь прошёл верификацию.

    Исключения (всегда пропускаем):
      - /start команда
      - сообщения в состоянии TokenState.waiting_token (обрабатывает main_menu)
      - все апдейты от ADMIN_ID
    """

    ALWAYS_ALLOW_COMMANDS = {"/start"}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event:   TelegramObject,
        data:    dict[str, Any],
    ) -> Any:
        from database import is_allowed, is_admin

        # Извлекаем user_id
        user_id = None
        if isinstance(event, Update):
            if event.message and event.message.from_user:
                user_id = event.message.from_user.id
            elif event.callback_query and event.callback_query.from_user:
                user_id = event.callback_query.from_user.id

        if user_id is None:
            return await handler(event, data)

        # Администратор — всегда пропускаем
        if is_admin(user_id):
            return await handler(event, data)

        # Команда /start — пропускаем (там живёт экран активации)
        if isinstance(event, Update) and event.message:
            text = (event.message.text or "").strip()
            if text.startswith("/start"):
                return await handler(event, data)

        # FSM: если пользователь в состоянии ввода токена — пропускаем
        state = data.get("state")
        if state:
            try:
                current = await state.get_state()
                if current == "TokenState:waiting_token":
                    return await handler(event, data)
            except Exception:
                pass

        # Проверяем доступ
        if not is_allowed(user_id):
            # Тихо игнорируем или предлагаем /start
            if isinstance(event, Update):
                if event.callback_query:
                    try:
                        await event.callback_query.answer(
                            "⛔ Нет доступа. Введи /start для активации.",
                            show_alert=True,
                        )
                    except Exception:
                        pass
                elif event.message:
                    try:
                        await event.message.answer(
                            "⛔ Нет доступа.\n\nВведи /start и активируй токен."
                        )
                    except Exception:
                        pass
            return  # не вызываем handler

        return await handler(event, data)


# ── Алерты ────────────────────────────────────────────────────────────────────

async def _send_alerts(bot):
    from database import (
        get_games, get_active_users,
        get_notification_last, set_notification_sent, get_weights,
    )
    from analytics import get_alerts, even_odd_streak, find_runs, gap_analysis, shannon_entropy
    from strategies import predict

    user_ids = get_active_users(hours=24)

    for uid in user_ids:
        try:
            last_sent = get_notification_last(uid)
            if last_sent and (datetime.now() - last_sent).total_seconds() < 3600:
                continue

            games = get_games(uid)
            if len(games) < 5:
                continue

            critical = []
            eo   = even_odd_streak(games)
            runs = find_runs(games)
            gaps = gap_analysis(games)
            H    = shannon_entropy(games, window=min(25, len(games)))
            total = len(games)

            if eo.get("current_streak", 0) >= 5:
                opposite = "нечётное" if eo["current_parity"] == "чётное" else "чётное"
                critical.append(
                    f"⚠️ {eo['current_streak']} раз подряд: {eo['current_parity']} — "
                    f"вероятен откат на {opposite}"
                )

            if runs.get("current_length", 0) >= 3:
                critical.append(
                    f"⚠️ Число {runs['current_value']} выпало "
                    f"{runs['current_length']} раза подряд"
                )

            overdue_thresh = max(12, int(total * 0.15))
            overdue = sorted([n for n, g in gaps.items() if g >= overdue_thresh])
            if overdue:
                critical.append(
                    f"⏳ Не выпадали {overdue_thresh}+ игр: "
                    f"{', '.join(str(n) for n in overdue)}"
                )

            if H / math.log2(11) < 0.55 and total >= 15:
                critical.append(f"🎯 Низкая энтропия ({H:.2f}) — паттерн предсказуем")

            weights = get_weights(uid)
            preds = predict(games, weights, top_n=3)
            if preds:
                top = preds[0]
                if top.get("strength", 0) >= 18:
                    critical.append(
                        f"🎯 Прогноз: {top['number']} "
                        f"(сила {top.get('strength', 0):.0f}/100, lift ×{top.get('lift', 1.0):.2f})"
                    )

            if critical:
                msg = "🔔 *Десятка — Алерт*\n\n" + "\n".join(critical)
                await bot.send_message(uid, msg, parse_mode="Markdown")
                set_notification_sent(uid)
                log.info(f"Alert → {uid}")

        except Exception as e:
            log.error(f"Alert error {uid}: {e}")


# ── Точка входа ────────────────────────────────────────────────────────────────

async def main():
    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.memory import MemoryStorage
    from aiogram.client.default import DefaultBotProperties

    from database import init_db, backup_db, create_history_snapshot
    from handlers import (
        main_menu_router, add_game_router, predict_router,
        stats_router, history_router, settings_router, admin_router,
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Токен не найден!\n"
            "Локально: .env → TELEGRAM_BOT_TOKEN=...\n"
            "Railway: Variables → TELEGRAM_BOT_TOKEN"
        )

    admin_id = os.getenv("ADMIN_ID", "0")
    if admin_id == "0":
        log.warning(
            "⚠️  ADMIN_ID не задан в .env! Контроль доступа отключён. "
            "Добавь ADMIN_ID=<твой Telegram ID> в .env"
        )

    init_db()
    log.info("БД готова.")

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="Markdown"))
    dp  = Dispatcher(storage=MemoryStorage())

    # Middleware доступа (на уровне всего диспетчера)
    dp.update.middleware(AccessMiddleware())

    # Роутеры: admin первым, main_menu вторым (содержит ввод токена)
    dp.include_router(admin_router)
    dp.include_router(main_menu_router)
    dp.include_router(add_game_router)
    dp.include_router(predict_router)
    dp.include_router(stats_router)
    dp.include_router(history_router)
    dp.include_router(settings_router)

    scheduler = AsyncIOScheduler()
    async def _backup_async():
        import asyncio
        await asyncio.to_thread(backup_db)
    async def _snapshot_async():
        import asyncio
        await asyncio.to_thread(create_history_snapshot)
    scheduler.add_job(_backup_async, "interval", hours=6)
    scheduler.add_job(_snapshot_async, "interval", hours=6)
    scheduler.add_job(_send_alerts, "interval", minutes=30, args=[bot])
    scheduler.start()
    try:
        await _snapshot_async()
        log.info("Стартовый snapshot истории сохранён.")
    except Exception as e:
        log.warning(f"Не удалось сохранить стартовый snapshot: {e}")

    log.info("Планировщик запущен (бэкап: 6ч · snapshot: 6ч · алерты: 30мин).")

    log.info("Бот v6 запускается…")
    try:
        await dp.start_polling(bot, drop_pending_updates=True)
    finally:
        scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
