import re
from datetime import datetime, timedelta
import discord
from discord.ext import commands, tasks

from config_manager import config
from services.storage_service import (
    load_current_duties, save_current_duties,
    load_pending_alerts, save_pending_alerts,
    load_last_message_id, save_last_message_id
)

def clean_name(name: str) -> str:
    if not name:
        return "Unknown"
    return re.sub(r'[\r\n\t]+', ' ', name).strip()

def format_duration(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours} ชม.")
    if minutes > 0:
        parts.append(f"{minutes} นาที")
    if seconds > 0 or not parts:
        parts.append(f"{seconds} วินาที")
    return " ".join(parts)

def parse_log_embed(embed: discord.Embed) -> dict | None:
    discord_id = None
    date_local = None
    time_local = None

    for field in embed.fields:
        if field.name and "PLAYER INFORMATION" in field.name.upper():
            m = re.search(r"\*\*Discord ID:\*\* (\d{17,20})", field.value)
            if m:
                discord_id = m.group(1).strip()
        elif field.name and "DATE (LOCAL)" in field.name.upper():
            date_local = field.value.strip()
        elif field.name and "TIME (LOCAL)" in field.name.upper():
            time_local = field.value.strip()

    if not all([discord_id, date_local, time_local]):
        return None

    full_time_str = f"{date_local} {time_local}"
    status = None
    if embed.description and "on duty" in embed.description.strip().lower():
        status = "on_duty"
    elif embed.description and "off duty" in embed.description.strip().lower():
        status = "off_duty"

    if status:
        return {
            "status": status,
            "discord_id": discord_id,
            "time_str": full_time_str
        }
    return None

class DutyCheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_off_duty_alerts.start()

    def cog_unload(self):
        self.check_off_duty_alerts.cancel()

    async def process_duty_message(self, message: discord.Message, embed: discord.Embed) -> str | None:
        log_data = parse_log_embed(embed)
        if not log_data:
            return None

        discord_id = log_data["discord_id"]
        guild = self.bot.get_guild(config.guild_id)
        member = guild.get_member(int(discord_id)) if guild else None
        player_name = clean_name(member.display_name) if member else f"Unknown User ({discord_id})"

        current_duties = await load_current_duties()
        pending_alerts = await load_pending_alerts()
        time_format = "%d/%m/%Y %H:%M:%S"
        max_gap = int(config.get("MAX_OFF_DUTY_GAP_SECONDS", 3600))
        alert_channel_id = config.get("ALERT_CHANNEL_ID")
        alert_channel = self.bot.get_channel(int(alert_channel_id)) if alert_channel_id else None

        processed_status = None

        if log_data["status"] == "on_duty":
            on_duty_time_str = log_data["time_str"]
            try:
                on_duty_dt = datetime.strptime(on_duty_time_str, time_format).replace(tzinfo=config.timezone)

                if discord_id in current_duties:
                    last_info = current_duties[discord_id]
                    last_status = last_info.get("last_status")
                    last_time_str = last_info.get("last_status_time")

                    if last_time_str:
                        last_dt = datetime.strptime(last_time_str, time_format).replace(tzinfo=config.timezone)

                        if last_status == "on_duty" and alert_channel:
                            elapsed = (on_duty_dt - last_dt).total_seconds()
                            emb = discord.Embed(
                                title="⚠️ แจ้งเตือน: เข้าเวรซ้อนกัน",
                                description=f"ผู้เล่น <@{discord_id}> เข้าเวรซ้อน",
                                color=discord.Color.orange()
                            )
                            emb.add_field(name="เวลาเดิม", value=f"`{last_time_str}`")
                            emb.add_field(name="เวลาใหม่", value=f"`{on_duty_time_str}`")
                            emb.add_field(name="รวมเวลาเดิม", value=format_duration(int(elapsed)))
                            await alert_channel.send(embed=emb)

                        elif last_status == "off_duty" and alert_channel:
                            gap = (on_duty_dt - last_dt).total_seconds()
                            if gap > max_gap:
                                emb = discord.Embed(
                                    title="⚠️ แจ้งเตือน: ออกเวรนานเกินกำหนด",
                                    description=f"ผู้เล่น <@{discord_id}> ออกเวรนานเกินกำหนด",
                                    color=discord.Color.red()
                                )
                                emb.add_field(name="เวลาออกเวรจริง", value=format_duration(int(gap)))
                                emb.add_field(name="เวลาสูงสุดที่อนุญาต", value=format_duration(max_gap))
                                emb.add_field(name="เกินกำหนดไป", value=format_duration(int(gap - max_gap)))
                                emb.add_field(name="เวลาออกเวรล่าสุด", value=f"`{last_time_str}`")
                                emb.add_field(name="เวลากลับเข้าเวร", value=f"`{on_duty_time_str}`")
                                await alert_channel.send(embed=emb)

                # เคลียร์ pending alerts
                if discord_id in pending_alerts:
                    del pending_alerts[discord_id]
                    await save_pending_alerts(pending_alerts)

                current_duties[discord_id] = {
                    "player_name": player_name,
                    "last_status": "on_duty",
                    "last_status_time": on_duty_time_str,
                    "last_status_message_link": message.jump_url
                }
                await save_current_duties(current_duties)
                processed_status = "on_duty"
            except Exception as e:
                print(f"[DutyCheckCog] Error processing on_duty: {e}")

        elif log_data["status"] == "off_duty":
            off_duty_time_str = log_data["time_str"]
            current_duties[discord_id] = {
                "player_name": player_name,
                "last_status": "off_duty",
                "last_status_time": off_duty_time_str,
                "last_status_message_link": message.jump_url
            }
            await save_current_duties(current_duties)
            processed_status = "off_duty"

        return processed_status

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not config.get("DUTY_TRACKER_ENABLED", False):
            return

        source_bot_id = config.get("SOURCE_BOT_ID")
        log_channel_id = config.get("LOG_CHANNEL_ID")

        if message.channel.id != int(log_channel_id) or message.author.id != int(source_bot_id):
            return

        if not message.embeds:
            return

        # ตรวจสอบว่าเคยใส่ emoji หรือยัง
        recorded_emoji_id = config.get("RECORDED_DUTY_EMOJI_ID")
        recorded_emoji = self.bot.get_emoji(int(recorded_emoji_id)) if recorded_emoji_id else None
        off_duty_emoji = "🔄️"

        for r in message.reactions:
            if recorded_emoji and r.emoji == recorded_emoji:
                return
            if str(r.emoji) == off_duty_emoji:
                return

        processed_status = None
        for embed in message.embeds:
            processed_status = await self.process_duty_message(message, embed)
            if processed_status:
                break

        if processed_status:
            try:
                if processed_status == "on_duty":
                    if recorded_emoji:
                        await message.add_reaction(recorded_emoji)
                    else:
                        await message.add_reaction("✅")
                elif processed_status == "off_duty":
                    await message.add_reaction(off_duty_emoji)
            except Exception as e:
                print(f"[DutyCheckCog] Reaction error: {e}")

        await save_last_message_id(message.id)

    @tasks.loop(minutes=1)
    async def check_off_duty_alerts(self):
        if not config.get("DUTY_TRACKER_ENABLED", False):
            return

        alert_channel_id = config.get("ALERT_CHANNEL_ID")
        if not alert_channel_id:
            return

        alert_channel = self.bot.get_channel(int(alert_channel_id))
        if not alert_channel:
            return

        current_duties = await load_current_duties()
        pending_alerts = await load_pending_alerts()
        time_format = "%d/%m/%Y %H:%M:%S"
        max_gap = int(config.get("MAX_OFF_DUTY_GAP_SECONDS", 3600))
        pre_announce_gap = int(config.get("ANNOUNCE_BEFORE_MAX_OFF_SECONDS", 600))
        alert_threshold = max_gap - pre_announce_gap

        now_local = datetime.now(config.timezone)
        guild = self.bot.get_guild(config.guild_id)

        for did, data in list(current_duties.items()):
            if data.get("last_status") == "off_duty":
                off_time_str = data.get("last_status_time")
                if not off_time_str:
                    continue
                try:
                    off_dt = datetime.strptime(off_time_str, time_format).replace(tzinfo=config.timezone)
                    elapsed = (now_local - off_dt).total_seconds()
                    curr_alert = pending_alerts.get(did, {}).get("alert_type")

                    # Pre-alert (เตือนล่วงหน้า)
                    if alert_threshold <= elapsed < max_gap and curr_alert != "pre_alert":
                        pname = data.get("player_name", f"<@{did}>")
                        emb = discord.Embed(
                            title="🔔 แจ้งเตือน: ใกล้ครบกำหนดออกเวรแล้ว",
                            description=(
                                f"<@{did}> ออกเวรมาแล้ว {format_duration(int(elapsed))}\n"
                                f"กรุณากลับเข้าเวรภายใน **{format_duration(pre_announce_gap)}**"
                            ),
                            color=discord.Color.yellow()
                        )
                        emb.add_field(name="เวลาออกเวร", value=f"`{off_time_str}`")
                        msg = await alert_channel.send(embed=emb)
                        pending_alerts[did] = {
                            "alert_type": "pre_alert",
                            "message_id": msg.id,
                            "channel_id": alert_channel.id
                        }
                        await save_pending_alerts(pending_alerts)

                except Exception as e:
                    print(f"[DutyCheckCog] Alert loop error for {did}: {e}")

    @check_off_duty_alerts.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

async def setup(bot: commands.Bot):
    await bot.add_cog(DutyCheckCog(bot))
