import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from aiogram import Bot
from supabase import AsyncClient

from database.repository import get_all_users
from ai.digest import generate_weekly_digest

async def _send_digest_to_users(bot: Bot, db: AsyncClient):
    logger.info("Starting weekly digest broadcast...")
    users = await get_all_users(db)
    
    count = 0
    for user in users:
        tg_id = user.get("tg_id")
        if not tg_id:
            continue
            
        try:
            digest_text = await generate_weekly_digest(user, db)
            if digest_text:
                await bot.send_message(chat_id=tg_id, text=digest_text, parse_mode="HTML")
                count += 1
            # Невелика затримка для запобігання rate limits Telegram
            await asyncio.sleep(1)  
        except Exception as e:
            logger.error(f"Failed to send digest to {tg_id}: {e}")
            
    logger.info(f"Weekly digest broadcast finished. Sent to {count} users.")


def setup_scheduler(bot: Bot, db: AsyncClient) -> AsyncIOScheduler:
    """Ініціалізує та налаштовує APScheduler для фонових задач."""
    scheduler = AsyncIOScheduler(timezone="Europe/Kyiv")
    
    # Відправка Weekly Digest. Наприклад, щонеділі об 11:00
    scheduler.add_job(
        _send_digest_to_users,
        trigger="cron",
        day_of_week="sun",
        hour=11,
        minute=0,
        kwargs={"bot": bot, "db": db},
        id="weekly_digest",
        replace_existing=True
    )
    
    return scheduler
