"""
Ініціалізація Bot, Dispatcher та підключення всіх роутерів і middleware.
Центральний "збиральник" всіх компонентів aiogram 3.x.
"""
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from supabase import AsyncClient

from bot.fsm_storage import SupabaseStorage
from bot.middlewares.db import DatabaseMiddleware
from bot.middlewares.auth import UserMiddleware
from bot.routers import onboarding, budget, ai_chat, document_handler, goals, history
from bot.config import get_settings
from aiogram.types import BotCommand


async def set_default_commands(bot: Bot):
    """Встановлює меню команд бота, яке показується при натисканні на '/'."""
    commands = [
        BotCommand(command="start", description="🏠 Головне меню / Налаштувати профіль"),
        BotCommand(command="budget", description="📊 Мій фінансовий звіт та залишок"),
        BotCommand(command="history", description="✏️ Останні транзакції (Редагування)"),
        BotCommand(command="goals", description="🎯 Мої фінансові цілі (Редагування)"),
        BotCommand(command="style", description="💬 Змінити стиль спілкування AI"),
        BotCommand(command="clear", description="🗑 Видалити всі мої дані назавжди"),
    ]
    await bot.set_my_commands(commands)


def create_bot_and_dispatcher(db: AsyncClient) -> tuple[Bot, Dispatcher]:
    """
    Фабрика — створює та налаштовує Bot і Dispatcher.

    Повертає кортеж (bot, dp) для запуску в bot/run.py.
    """
    settings = get_settings()
    # --- Bot ---
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        # HTML parse_mode більш толерантний до спеціальних символів
        # ніж MarkdownV2 — знижує ризик TelegramBadRequest від LLM виводу
    )

    # --- Storage для FSM ---
    # Зберігаємо всі стани і дані FSM в Supabase
    storage = SupabaseStorage(db)

    # --- Dispatcher ---
    dp = Dispatcher(storage=storage)

    # --- Middleware (порядок важливий!) ---
    # DatabaseMiddleware — першою, бо UserMiddleware потребує db
    dp.update.middleware(DatabaseMiddleware(db))
    dp.update.middleware(UserMiddleware())

    # --- Роутери (порядок = пріоритет обробки) ---
    # 1. Onboarding — перехоплює /start та onboarding FSM стани
    dp.include_router(onboarding.router)
    # 2. CSV — обробляє Document updates (файли)
    dp.include_router(document_handler.router)
    # 3. Goals — команди /goals та FSM для цілей
    dp.include_router(goals.router)
    # 4. Budget — явні команди /budget, /add
    dp.include_router(budget.router)
    # 4.5. History — останні транзакції
    dp.include_router(history.router)
    # 5. AI Chat — catchall для будь-якого тексту (повинен бути ОСТАННІМ)
    dp.include_router(ai_chat.router)

    return bot, dp
