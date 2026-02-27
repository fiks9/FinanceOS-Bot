"""
Точка входу бота — FinanceOS Bot.
Запуск: python -m bot.run  (або через Procfile на Railway)
"""
import asyncio

from loguru import logger

from bot.config import get_settings
from bot.setup import create_bot_and_dispatcher, set_default_commands
from database.client import get_supabase


async def main() -> None:
    """Ініціалізує всі компоненти та запускає polling."""
    settings = get_settings()
    logger.info(f"Starting FinanceOS Bot | env={settings.environment}")

    # Ініціалізуємо Supabase клієнт (singleton)
    db = await get_supabase()
    logger.info("Supabase client initialized ✓")

    # Створюємо Bot і Dispatcher з усіма роутерами та middleware
    bot, dispatcher = create_bot_and_dispatcher(db)

    try:
        # Встановлюємо меню команд (іконка / в боті)
        await set_default_commands(bot)
        
        # Запускаємо фонові задачі (дайджести)
        from bot.scheduler import setup_scheduler
        scheduler = setup_scheduler(bot, db)
        scheduler.start()
        logger.info("Scheduler started ✓")

        logger.info("Bot started polling...")
        await dispatcher.start_polling(bot, drop_pending_updates=True)
    finally:
        logger.info("Bot shutdown complete.")
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
