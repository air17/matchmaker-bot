import asyncio
import os
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

import database as db
import handlers

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
INITIAL_ADMIN_ID = os.getenv("INITIAL_ADMIN_ID")
INITIAL_ADMIN_USERNAME = os.getenv("INITIAL_ADMIN_USERNAME")

async def main():
    logging.basicConfig(level=logging.INFO)
    
    # Initialize DB
    db.init_db()
    if INITIAL_ADMIN_ID and INITIAL_ADMIN_USERNAME:
        db.add_admin(int(INITIAL_ADMIN_ID), INITIAL_ADMIN_USERNAME)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    
    dp.include_router(handlers.router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
