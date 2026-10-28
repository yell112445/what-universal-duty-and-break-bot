import os
import sys
import asyncio
import logging
from datetime import datetime

# บังคับ Unbuffered / Line Buffering เพื่อให้แสดงผลบน Web Console ของ Dashboard ทันที
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(line_buffering=True, encoding="utf-8")
    except Exception:
        pass

import discord
from discord.ext import commands

from config_manager import config

# ตั้งค่า Logging ให้แสดงผลภาษาไทยและ Timestamp ชัดเจนบน Console (Pterodactyl)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("WhatUniversalBot")

class WhatUniversalBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.reactions = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None
        )

    async def setup_hook(self):
        """โหลด Cogs ทั้งหมดและลงทะเบียน Persistent Views และ Slash Commands"""
        logger.info("กำลังโหลดโมดูลและ Cogs...")

        cogs = [
            "cogs.config_cog",
            "cogs.operations",
            "cogs.breaktime",
            "cogs.duty_check",
            "cogs.introduction",
            "cogs.ui_preview"
        ]

        for cog in cogs:
            try:
                await self.load_extension(cog)
                logger.info(f"✅ โหลด Cog สำเร็จ: {cog}")
            except Exception as e:
                logger.error(f"❌ โหลด Cog {cog} ล้มเหลว: {e}", exc_info=True)

        # Sync Slash Commands ทั้งแบบ Guild (อัปเดตทันที) และแบบ Global
        try:
            guild_obj = discord.Object(id=config.guild_id)
            self.tree.copy_global_to(guild=guild_obj)
            synced_guild = await self.tree.sync(guild=guild_obj)
            synced_global = await self.tree.sync()
            logger.info(f"⚡ Sync Slash Commands สำเร็จ: Guild {config.guild_id} ({len(synced_guild)} คำสั่ง), Global ({len(synced_global)} คำสั่ง)")
        except Exception as e:
            logger.error(f"⚠️ เกิดข้อผิดพลาดขณะ Sync Slash Commands: {e}")

    async def on_ready(self):
        logger.info("=" * 60)
        logger.info(f"🚀 บอทออนไลน์แล้วในชื่อ: {self.user} (ID: {self.user.id})")
        logger.info(f"📌 เวอร์ชัน: {config.get('BOT_VERSION')}")
        logger.info(f"🌐 เซิร์ฟเวอร์เป้าหมาย: What Universal ({config.guild_id})")
        logger.info(f"🕒 Timezone: {config.timezone}")
        logger.info("=" * 60)

        # เริ่มต้นแผง Breaktime Panel & Board
        try:
            breaktime_cog = self.get_cog("BreaktimeCog")
            if breaktime_cog:
                await breaktime_cog.ensure_panel_message()
                await breaktime_cog.update_leave_board()
                logger.info("🌴 ซิงค์แผงและบอร์ดลางานเริ่มต้นสำเร็จ")
        except Exception as e:
            logger.warning(f"⚠️ ไม่สามารถซิงค์ Leave Panel เริ่มต้นได้: {e}")

async def main():
    token = config.token
    if not token or token == "your_bot_token_here":
        logger.critical("❌ ไม่พบ Token ของบอท! กรุณาระบุ DISCORD_TOKEN ในไฟล์ .env หรือตั้งค่าในแผงควบคุมโฮสต์")
        return

    bot = WhatUniversalBot()
    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("บอทถูกหยุดการทำงานโดยผู้ใช้ (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"เกิดข้อผิดพลาดร้ายแรง: {e}", exc_info=True)
