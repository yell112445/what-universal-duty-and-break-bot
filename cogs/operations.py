import io
import os
import re
import random
import shutil
import tempfile
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput, View, Button
from openpyxl import Workbook

from config_manager import config
from services.storage_service import (
    load_operation_status, save_operation_status,
    load_operation_daily_data, save_operation_session_data,
    OPERATIONS_DIR, DATA_DIR
)

logger = logging.getLogger("Operations")

def clean_name(name: str) -> str:
    if not name:
        return "Unknown"
    return name.replace("\r", " ").replace("\n", " ").strip()

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

def generate_otp() -> str:
    return f"{random.randint(0, 9999):04d}"

class OTPModal(Modal):
    def __init__(self, opener_id: int):
        super().__init__(title="OTP Verification")
        self.opener_id = opener_id
        self.otp_field = TextInput(
            label="รหัส OTP 4 หลัก",
            style=discord.TextStyle.short,
            placeholder="กรอกรหัส 4 หลัก เช่น 1234",
            min_length=4,
            max_length=4,
            required=True
        )
        self.add_item(self.otp_field)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.opener_id:
            return await interaction.response.send_message("❌ หน้านี้ไม่ใช่ของคุณ", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        op_status = await load_operation_status()
        if not op_status.get("status_message_id"):
            return await interaction.followup.send("ขณะนี้ไม่มีรอบการทำงานที่เปิดอยู่", ephemeral=True)

        current_otp = op_status.get("otp")
        if not current_otp:
            return await interaction.followup.send("ไม่พบ OTP ในระบบ โปรดลองอีกครั้ง", ephemeral=True)

        expires_iso = op_status.get("otp_expires_at")
        if expires_iso:
            try:
                exp_dt = datetime.fromisoformat(expires_iso)
                if datetime.now(config.timezone) > exp_dt:
                    return await interaction.followup.send("❌ OTP หมดอายุแล้ว โปรดรอ OTP รอบถัดไป", ephemeral=True)
            except Exception:
                pass

        if self.otp_field.value.strip() != current_otp:
            return await interaction.followup.send(f"❌ รหัส OTP ไม่ถูกต้อง กรุณาดูในห้อง <#{config.get('OPERATION_OTP_CHANNEL')}>", ephemeral=True)

        user_id = str(interaction.user.id)
        verified = op_status.get("otp_verified_users", {}) or {}
        verified[user_id] = datetime.now(config.timezone).isoformat()
        op_status["otp_verified_users"] = verified

        # บันทึกเริ่มการทำงานทันที
        now_dt = datetime.now(config.timezone)
        pname = clean_name(interaction.user.display_name)
        start_str = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        op_status[user_id] = {
            "player_name": pname,
            "start_time": start_str
        }
        await save_operation_status(op_status)

        # ส่ง Personal Log: Check-in
        cog = interaction.client.get_cog("OperationsCog")
        if cog and hasattr(cog, "send_personal_log"):
            await cog.send_personal_log(
                user_id=user_id,
                player_name=pname,
                start_time=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_time="กำลังทำงาน",
                duration=0,
                source="user_checkin"
            )

        embed = discord.Embed(
            title="CHECK-IN SUCCESS",
            description=(
                f"**ผู้ใช้งาน:** {interaction.user.mention}\n"
                f"• สถานะ: `Online` (กำลังทำงาน)\n"
                f"• เวลาเริ่มงาน: `{now_dt.strftime('%H:%M:%S')}`\n"
                f"• วันที่: `{now_dt.strftime('%d/%m/%Y')}`\n\n"
                f"-# ยืนยัน OTP `{self.otp_field.value.strip()}` สำเร็จ เริ่มบันทึกเวลาทำงานแล้ว"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

class OperationView(View):
    def __init__(self):
        super().__init__(timeout=None)

    # ปุ่ม 1: ฟ้า (Primary) - Check-in
    @discord.ui.button(label="Check-in", style=discord.ButtonStyle.primary, custom_id="operation_checkin_button", row=0)
    async def checkin_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        user_id = str(user.id)
        op_status = await load_operation_status()

        if not op_status.get("status_message_id"):
            return await interaction.response.send_message(
                "ขณะนี้ยังไม่มีรอบการทำงานที่เปิดอยู่ กรุณารอเปิดรอบใหม่ครับ",
                ephemeral=True
            )

        # ถ้าเริ่มงานอยู่แล้ว
        if user_id in op_status and isinstance(op_status[user_id], dict) and "start_time" in op_status[user_id]:
            start_str = op_status[user_id].get("start_time", "")
            return await interaction.response.send_message(
                f"⚠️ คุณกำลัง Check-in ทำงานอยู่แล้ว (เริ่มตั้งแต่ `{start_str.split('.')[0]}`)\nหากต้องการเลิกงานให้กดปุ่ม **Check-out** ครับ",
                ephemeral=True
            )

        # ตรวจสอบการยืนยัน OTP
        verified_users = op_status.get("otp_verified_users", {}) or {}
        if user_id not in verified_users:
            return await interaction.response.send_modal(OTPModal(user.id))

        await interaction.response.defer(ephemeral=True, thinking=True)
        now_dt = datetime.now(config.timezone)
        pname = clean_name(user.display_name)
        start_time_str = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        op_status[user_id] = {
            "player_name": pname,
            "start_time": start_time_str
        }
        await save_operation_status(op_status)

        cog = interaction.client.get_cog("OperationsCog")
        if cog and hasattr(cog, "send_personal_log"):
            await cog.send_personal_log(
                user_id=user_id,
                player_name=pname,
                start_time=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                end_time="กำลังทำงาน",
                duration=0,
                source="user_checkin"
            )

        embed = discord.Embed(
            title="CHECK-IN SUCCESS",
            description=(
                f"**ผู้ใช้งาน:** {interaction.user.mention}\n"
                f"• สถานะ: `Online` (กำลังทำงาน)\n"
                f"• เวลาเริ่มงาน: `{now_dt.strftime('%H:%M:%S')}`\n"
                f"• วันที่: `{now_dt.strftime('%d/%m/%Y')}`\n\n"
                f"-# เริ่มบันทึกเวลาทำงานเรียบร้อยแล้ว ขอให้มีความสุขกับการทำงานครับ"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ปุ่ม 2: เทา (Secondary) - Check-out
    @discord.ui.button(label="Check-out", style=discord.ButtonStyle.secondary, custom_id="operation_checkout_button", row=0)
    async def checkout_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        user_id = str(user.id)
        op_status = await load_operation_status()

        if not op_status.get("status_message_id"):
            return await interaction.response.send_message(
                "ขณะนี้ไม่มีรอบการทำงานที่เปิดอยู่",
                ephemeral=True
            )

        if user_id not in op_status or not isinstance(op_status[user_id], dict) or "start_time" not in op_status[user_id]:
            return await interaction.response.send_message(
                "⚠️ คุณยังไม่ได้กด Check-in เริ่มงานในรอบนี้ครับ",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        now_dt = datetime.now(config.timezone)
        start_time_str = op_status[user_id].get("start_time")
        try:
            start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
        except Exception:
            start_dt = now_dt

        duration = int((now_dt - start_dt).total_seconds())
        pname = clean_name(op_status[user_id].get("player_name", user.display_name))
        end_time_str = now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")

        # บันทึกลงไฟล์รายวัน
        await save_operation_session_data(
            user_id=user_id,
            player_name=pname,
            start=start_time_str,
            end=end_time_str,
            duration=duration,
            source="user_checkout"
        )

        op_status.setdefault("recent_sessions", []).append({
            "discord_id": user_id,
            "player_name": pname,
            "start_time": start_time_str,
            "end_time": end_time_str,
            "duration_seconds": duration,
            "source": "user_checkout"
        })
        del op_status[user_id]
        await save_operation_status(op_status)

        cog = interaction.client.get_cog("OperationsCog")
        if cog and hasattr(cog, "send_personal_log"):
            clean_start = start_time_str.split(".")[0] if "." in start_time_str else start_time_str
            await cog.send_personal_log(
                user_id=user_id,
                player_name=pname,
                start_time=clean_start,
                end_time=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                duration=duration,
                source="user_checkout"
            )

        clean_start_display = start_time_str.split(" ")[1].split(".")[0] if " " in start_time_str else start_time_str
        embed = discord.Embed(
            title="CHECK-OUT SUCCESS",
            description=(
                f"**ผู้ใช้งาน:** {interaction.user.mention}\n"
                f"• Check-in: `{clean_start_display}`\n"
                f"• Check-out: `{now_dt.strftime('%H:%M:%S')}`\n"
                f"• รวมเวลาทำงานรอบนี้: `{format_duration(duration)}`\n\n"
                f"-# ระบบบันทึกข้อมูลเรียบร้อยแล้ว ขอบคุณสำหรับการทำงานครับ"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ปุ่ม 3: เทา (Secondary) - My Status
    @discord.ui.button(label="My Status", style=discord.ButtonStyle.secondary, custom_id="operation_status_button", row=0)
    async def status_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        user = interaction.user
        user_id = str(user.id)
        op_status = await load_operation_status()
        now_dt = datetime.now(config.timezone)

        # คำนวณเวลารวมวันนี้จาก daily data
        daily_data = await load_operation_daily_data(now_dt)
        user_daily = daily_data.get(user_id, {})
        past_dur = sum(int(s.get("duration_seconds", 0)) for s in user_daily.get("sessions", []))

        is_online = user_id in op_status and isinstance(op_status[user_id], dict) and "start_time" in op_status[user_id]

        embed = discord.Embed(
            title="STATUS REPORT",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        if is_online:
            start_str = op_status[user_id].get("start_time")
            try:
                st_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
                curr_dur = int((now_dt - st_dt).total_seconds())
                start_ts = int(st_dt.timestamp())
            except Exception:
                curr_dur = 0
                start_ts = int(now_dt.timestamp())

            total_today = past_dur + curr_dur
            embed.add_field(name="สถานะปัจจุบัน", value="`Online` (กำลังทำงาน)", inline=True)
            embed.add_field(name="เริ่มงานเมื่อ", value=f"<t:{start_ts}:R>", inline=True)
            embed.add_field(name="รอบปัจจุบัน", value=f"`{format_duration(curr_dur)}`", inline=True)
            embed.add_field(name="เวลารวมวันนี้", value=f"`{format_duration(total_today)}`", inline=False)
        else:
            embed.add_field(name="สถานะปัจจุบัน", value="`Offline` (ไม่ได้ลงเวลา)", inline=True)
            embed.add_field(name="เวลารวมวันนี้", value=f"`{format_duration(past_dur)}`" if past_dur > 0 else "`ยังไม่มีประวัติวันนี้`", inline=True)

        embed.set_footer(text="What Universal • ข้อมูลเรียลไทม์")
        await interaction.followup.send(embed=embed, ephemeral=True)

class OperationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.otp_rotate_loop.start()
        self.check_auto_stop_loop.start()
        self.refresh_status_message_loop.start()

    def cog_unload(self):
        self.otp_rotate_loop.cancel()
        self.check_auto_stop_loop.cancel()
        self.refresh_status_message_loop.cancel()

    def is_assistant_or_above(self, interaction: discord.Interaction) -> bool:
        return config.is_assistant_or_above(interaction.user.id)

    async def get_channel(self, channel_id_or_name: Any) -> Optional[discord.abc.GuildChannel]:
        """ดึงอ็อบเจ็กต์ช่องโดยค้นหาจาก Cache ก่อน หากไม่พบจะ Fetch ตรงจาก Discord API"""
        if not channel_id_or_name:
            return None

        # กรณีเป็น Channel ID (int หรือ string ตัวเลข)
        if isinstance(channel_id_or_name, int) or (isinstance(channel_id_or_name, str) and channel_id_or_name.isdigit()):
            cid = int(channel_id_or_name)
            ch = self.bot.get_channel(cid)
            if ch is not None:
                return ch
            try:
                ch = await self.bot.fetch_channel(cid)
                return ch
            except Exception as e:
                logger.warning(f"ไม่สามารถ fetch_channel({cid}) ได้: {e}")
                return None

        # กรณีเป็นชื่อห้อง (string)
        if isinstance(channel_id_or_name, str):
            for guild in self.bot.guilds:
                for c in guild.text_channels:
                    if c.name == channel_id_or_name:
                        return c
        return None

    async def send_assistant_log(self, title: str, description: str, color: discord.Color = discord.Color.blurple()):
        """ส่ง Log ไปยังห้อง OPERATION_LOG_ASSISTANT_CHANNEL_ID"""
        ch_id = config.get("OPERATION_LOG_ASSISTANT_CHANNEL_ID")
        if not ch_id:
            return
        ch = await self.get_channel(ch_id)
        if ch:
            emb = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=datetime.now(config.timezone)
            )
            try:
                await ch.send(embed=emb)
            except Exception as e:
                logger.error(f"ไม่สามารถส่ง Assistant Log: {e}")
        else:
            logger.warning(f"ไม่พบห้อง Assistant Log ID: {ch_id}")

    async def send_personal_log(
        self,
        user_id: str,
        player_name: str,
        start_time: str,
        end_time: str,
        duration: int,
        source: str
    ):
        """ส่ง Personal Log ไปยังห้อง OPERATION_PERSONAL_LOG_CHANNELS หรือ fallback ไปห้อง OPERATION_LOG_CHANNEL_ID"""
        try:
            ch_target = config.get("OPERATION_PERSONAL_LOG_CHANNELS") or config.get("OPERATION_LOG_CHANNEL_ID")
            ch = await self.get_channel(ch_target)
            if not ch:
                logger.warning(f"ไม่พบห้อง Personal Log (เป้าหมาย: {ch_target})")
                return

            c_blurple = discord.Color.from_rgb(88, 101, 242)
            c_slate = discord.Color.from_rgb(47, 49, 54)
            c_amber = discord.Color.from_rgb(245, 158, 11)

            color = c_blurple
            source_tag = "Check-in"

            if source == "user_checkout":
                source_tag = "Check-out"
                color = c_slate
            elif source == "user_checkin":
                source_tag = "Check-in"
                color = c_blurple
            elif source.startswith("force_stop_by:"):
                admin_id = source.split(":")[1]
                source_tag = f"Force Stop by <@{admin_id}>"
                color = c_amber
            elif source == "auto_stop":
                source_tag = "Auto Stop (Time Reached)"
                color = c_amber
            elif source == "assistant_stop":
                source_tag = "Session Closed"
                color = c_slate

            embed = discord.Embed(
                title="TIME TRACKER — RECEIPT",
                color=color,
                timestamp=datetime.now(config.timezone)
            )
            embed.description = (
                f"**ผู้ใช้งาน:** <@{user_id}> (`{player_name}`)\n"
                f"• สถานะ: `[{source_tag}]`\n"
                f"• เริ่มต้น: `{start_time}`\n"
                f"• สิ้นสุด: `{end_time}`\n"
                f"• รวมเวลา: `{format_duration(duration) if duration > 0 else '-'}`\n\n"
                f"-# บันทึกข้อมูลเข้าสู่ระบบเรียบร้อยแล้ว"
            )
            embed.set_footer(text="What Universal Time Tracker")

            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"เกิดข้อผิดพลาดในการส่ง Personal Log: {e}")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """รองรับปุ่มเดิม operation_stamp_button เพื่อความต่อเนื่อง 100%"""
        if interaction.type == discord.InteractionType.component:
            cid = interaction.data.get("custom_id")
            if cid == "operation_stamp_button":
                user_id = str(interaction.user.id)
                op_status = await load_operation_status()
                if not op_status.get("status_message_id"):
                    return await interaction.response.send_message("ขณะนี้ไม่มีรอบการทำงานที่เปิดอยู่", ephemeral=True)
                view = OperationView()
                if user_id in op_status and isinstance(op_status[user_id], dict) and "start_time" in op_status[user_id]:
                    await view.checkout_button.callback(view, interaction)
                else:
                    await view.checkin_button.callback(view, interaction)

    # --- Background Loops ---
    @tasks.loop(minutes=1)
    async def otp_rotate_loop(self):
        """หมุนเวียนรหัส OTP ทุกๆ X นาทีตามกำหนด"""
        try:
            op_status = await load_operation_status()
            if not op_status.get("status_message_id"):
                return

            now_dt = datetime.now(config.timezone)
            expires_iso = op_status.get("otp_expires_at")
            needs_new_otp = False

            if not expires_iso:
                needs_new_otp = True
            else:
                try:
                    exp_dt = datetime.fromisoformat(expires_iso)
                    if now_dt >= exp_dt:
                        needs_new_otp = True
                except Exception:
                    needs_new_otp = True

            if needs_new_otp:
                otp_min = int(config.get("OTP_EXPIRY_MINUTES", 20))
                new_otp = generate_otp()
                new_exp = now_dt + timedelta(minutes=otp_min)

                op_status["otp"] = new_otp
                op_status["otp_generated_at"] = now_dt.isoformat()
                op_status["otp_expires_at"] = new_exp.isoformat()

                # ส่งหรืออัปเดตข้อความในช่อง OTP
                otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
                if otp_ch_id:
                    ch = await self.get_channel(otp_ch_id)
                    if ch:
                        embed = discord.Embed(
                            title="OTP PASSCODE — TIME TRACKER",
                            description=(
                                f"รหัสสำหรับใช้ Check-in ประจำรอบนี้:\n\n"
                                f"# `{new_otp}`\n\n"
                                f"• เปลี่ยนรหัสทุก: `{otp_min} นาที`\n"
                                f"• หมดอายุใน: <t:{int(new_exp.timestamp())}:R>\n\n"
                                f"-# กรุณานำรหัส 4 หลักไปกรอกในปุ่ม Check-in เพื่อเริ่มบันทึกเวลา"
                            ),
                            color=discord.Color.from_rgb(88, 101, 242),
                            timestamp=now_dt
                        )
                        embed.set_footer(text="What Universal OTP Service")

                        old_msg_id = op_status.get("otp_message_id")
                        sent_new = False
                        if old_msg_id:
                            try:
                                old_msg = await ch.fetch_message(int(old_msg_id))
                                await old_msg.edit(embed=embed)
                            except Exception:
                                sent_new = True
                        else:
                            sent_new = True

                        if sent_new:
                            try:
                                new_msg = await ch.send(embed=embed)
                                op_status["otp_message_id"] = new_msg.id
                            except Exception as e:
                                logger.error(f"ไม่สามารถส่งข้อความ OTP ใหม่: {e}")

                await save_operation_status(op_status)
        except Exception as e:
            logger.error(f"[OperationsCog] otp_rotate_loop error: {e}")

    @tasks.loop(seconds=15)
    async def refresh_status_message_loop(self):
        """อัปเดต Embed สถานะกำลังพลที่ปฏิบัติการอยู่แบบ Realtime"""
        try:
            op_status = await load_operation_status()
            status_msg_id = op_status.get("status_message_id")
            status_ch_id = op_status.get("status_channel_id")

            if not status_msg_id or not status_ch_id:
                return

            ch = await self.get_channel(status_ch_id)
            if not ch:
                return

            try:
                msg = await ch.fetch_message(int(status_msg_id))
            except Exception:
                return

            role_mapping = config.role_mapping
            guild = ch.guild

            # จัดกลุ่ม active users
            unit_groups: Dict[str, List[str]] = {name: [] for name in role_mapping.values()}
            unit_groups["หน่วยอื่น ๆ / ไม่ระบุ"] = []

            now_dt = datetime.now(config.timezone)
            active_count = 0

            for k, v in op_status.items():
                if isinstance(k, str) and k.isdigit():
                    active_count += 1
                    pname = v.get("player_name", f"User {k}")
                    start_str = v.get("start_time", "")
                    duration_str = ""
                    try:
                        st_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
                        duration_str = format_duration(int((now_dt - st_dt).total_seconds()))
                    except Exception:
                        pass

                    # หาหน่วยจาก Role
                    member = guild.get_member(int(k))
                    matched_unit = "หน่วยอื่น ๆ / ไม่ระบุ"
                    if member:
                        for rid, rname in role_mapping.items():
                            if member.get_role(rid):
                                matched_unit = rname
                                break

                    unit_groups[matched_unit].append(f"• <@{k}> — `{duration_str}`")

            embed = discord.Embed(
                title="WHAT UNIVERSAL — TIME TRACKER",
                description=(
                    f"-# ระบบลงเวลาทำงานและตรวจสอบรายชื่อคนออนไลน์\n\n"
                    f"**สถานะ:** `Active` • กำลังทำงานอยู่ **{active_count}** คน"
                ),
                color=discord.Color.from_rgb(88, 101, 242) if active_count > 0 else discord.Color.from_rgb(47, 49, 54),
                timestamp=now_dt
            )

            for unit_name, members in unit_groups.items():
                if members:
                    embed.add_field(name=f"{unit_name} ({len(members)})", value="\n".join(members[:20]), inline=False)

            embed.set_footer(text=f"อัปเดตอัตโนมัติทุก 15 วินาที • {config.get('BOT_VERSION')}")
            await msg.edit(embed=embed)
        except Exception as e:
            logger.error(f"[OperationsCog] refresh_status_message_loop error: {e}")

    @tasks.loop(minutes=1)
    async def check_auto_stop_loop(self):
        """ตรวจสอบและหยุดรอบปฏิบัติการอัตโนมัติตามเวลาที่กำหนดไว้"""
        try:
            op_status = await load_operation_status()
            if not op_status.get("status_message_id"):
                return

            now_local = datetime.now(config.timezone)
            now_time_str = now_local.strftime("%H:%M")
            auto_stop_times = config.get("AUTO_STOP_TIMES", [])
            temp_stop = op_status.get("temp_auto_stop")

            # ตรวจสอบเวลาหยุด (รอบปกติจาก config หรือรอบที่ตั้งชั่วคราว)
            if now_time_str in auto_stop_times or (temp_stop and now_time_str == temp_stop):
                logger.info(f"[OperationsCog] Auto-stop triggered at {now_time_str} (temp_stop={temp_stop})")
                await self.execute_stop(interaction=None, source="auto_stop")
                return

            # แจ้งเตือนล่วงหน้าก่อนปิดรอบ
            announce_min = int(config.get("AUTO_STOP_ANNOUNCE_MINUTES", 10))
            future_time_str = (now_local + timedelta(minutes=announce_min)).strftime("%H:%M")
            if future_time_str in auto_stop_times or (temp_stop and future_time_str == temp_stop):
                last_announced = op_status.get("last_auto_stop_announced")
                if last_announced != future_time_str:
                    op_status["last_auto_stop_announced"] = future_time_str
                    await save_operation_status(op_status)

                    status_ch_id = op_status.get("status_channel_id")
                    if status_ch_id:
                        ch = await self.get_channel(status_ch_id)
                        if ch:
                            embed = discord.Embed(
                                title="NOTICE: SESSION ENDING SOON",
                                description=(
                                    f"ระบบจะปิดรอบบันทึกเวลาอัตโนมัติในอีก **{announce_min} นาที**\n"
                                    f"• เวลาปิดรอบ: `{future_time_str}` น.\n\n"
                                    f"-# กรุณากด Check-out หรือเตรียมความพร้อมก่อนหมดเวลาครับ"
                                ),
                                color=discord.Color.from_rgb(245, 158, 11),
                                timestamp=now_local
                            )
                            embed.set_footer(text="What Universal Auto-Stop Warning")
                            await ch.send(embed=embed)
        except Exception as e:
            logger.error(f"[OperationsCog] check_auto_stop_loop error: {e}")

    @otp_rotate_loop.before_loop
    @refresh_status_message_loop.before_loop
    @check_auto_stop_loop.before_loop
    async def before_op_loops(self):
        await self.bot.wait_until_ready()

    # --- Operation Control Logic ---
    async def execute_stop(self, interaction: Optional[discord.Interaction] = None, source: str = "manual"):
        op_status = await load_operation_status()
        if not op_status.get("status_message_id"):
            if interaction:
                await interaction.followup.send("ℹ️ ขณะนี้ไม่มีรอบการทำงานที่เปิดอยู่", ephemeral=True)
            return

        now_dt = datetime.now(config.timezone)
        summary_records = []

        # ปิด session ของผู้ใช้ที่ยังค้างอยู่
        for k, v in list(op_status.items()):
            if isinstance(k, str) and k.isdigit():
                pname = v.get("player_name", f"User {k}")
                start_str = v.get("start_time", "")
                try:
                    st_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
                    duration = int((now_dt - st_dt).total_seconds())
                except Exception:
                    duration = 0

                await save_operation_session_data(
                    user_id=k,
                    player_name=pname,
                    start=start_str,
                    end=now_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
                    duration=duration,
                    source=source
                )
                summary_records.append((k, pname, duration))

                # ส่ง Personal Log สำหรับคนที่ถูกสั่งปิดรอบ
                clean_start = start_str.split(".")[0] if "." in start_str else start_str
                await self.send_personal_log(
                    user_id=k,
                    player_name=pname,
                    start_time=clean_start,
                    end_time=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    duration=duration,
                    source=source
                )

        # ลบข้อความปุ่มและ OTP
        button_msgs = op_status.get("button_messages", []) or []
        for bm in button_msgs:
            try:
                ch = await self.get_channel(bm["channel_id"])
                if ch:
                    m = await ch.fetch_message(int(bm["message_id"]))
                    await m.delete()
            except Exception:
                pass

        otp_msg_id = op_status.get("otp_message_id")
        otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
        if otp_msg_id and otp_ch_id:
            try:
                ch = await self.get_channel(otp_ch_id)
                if ch:
                    m = await ch.fetch_message(int(otp_msg_id))
                    await m.delete()
            except Exception:
                pass

        # แก้ไขข้อความสถานะเดิมเป็นสรุปผล
        status_msg_id = op_status.get("status_message_id")
        status_ch_id = op_status.get("status_channel_id")
        if status_msg_id and status_ch_id:
            try:
                ch = await self.get_channel(status_ch_id)
                if ch:
                    msg = await ch.fetch_message(int(status_msg_id))
                    embed = discord.Embed(
                        title="SESSION CONCLUDED",
                        description=(
                            f"รอบบันทึกเวลาสิ้นสุดลงเมื่อ `{now_dt.strftime('%H:%M:%S')}`\n"
                            f"-# สรุปภาพรวมชั่วโมงการทำงานของทุกคนในรอบนี้"
                        ),
                        color=discord.Color.from_rgb(47, 49, 54),
                        timestamp=now_dt
                    )
                    lines = [f"• <@{uid}> — `{format_duration(dur)}`" for uid, name, dur in summary_records]
                    embed.add_field(name="สรุปเวลาทำงานรอบนี้", value="\n".join(lines[:25]) if lines else "*(ไม่มีผู้ลงเวลาค้างในรอบนี้)*")
                    await msg.edit(embed=embed, view=None)
            except Exception as e:
                logger.error(f"[OperationsCog] Failed to edit status message on stop: {e}")

        # เคลียร์สถานะ
        await save_operation_status({})

        caller = interaction.user.display_name if interaction else "Auto Stop"
        await self.send_assistant_log(
            "SESSION CLOSED",
            f"ผู้สั่งการ: **{caller}**\nเวลา: `{now_dt.strftime('%d/%m/%Y %H:%M:%S')}`",
            color=discord.Color.from_rgb(47, 49, 54)
        )

        if interaction:
            await interaction.followup.send("✅ ปิดรอบการทำงานและสรุปผลเรียบร้อยแล้ว", ephemeral=True)

    # --- Slash Commands ---
    @app_commands.command(name="operation_start", description="เริ่มรอบบันทึกเวลาใหม่ และสร้างปุ่มลงเวลา")
    @app_commands.describe(stop_time="เวลาปิดรอบอัตโนมัติเฉพาะรอบนี้ (HH:MM เช่น 18:00 หรือ 03:00) ถ้าไม่ใส่จะยึดตาม config")
    async def operation_start(self, interaction: discord.Interaction, stop_time: Optional[str] = None):
        if not self.is_assistant_or_above(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        if stop_time:
            stop_time = stop_time.strip()
            if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", stop_time):
                return await interaction.response.send_message("❌ รูปแบบเวลาไม่ถูกต้อง (ใช้ HH:MM เช่น 18:00 หรือ 03:00)", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        op_status = await load_operation_status()
        if op_status.get("status_message_id"):
            return await interaction.followup.send("⚠️ มีรอบการทำงานเปิดอยู่แล้ว", ephemeral=True)

        now_dt = datetime.now(config.timezone)
        embed = discord.Embed(
            title="WHAT UNIVERSAL — TIME TRACKER",
            description=(
                f"-# ระบบลงเวลาทำงานและตรวจสอบรายชื่อคนออนไลน์\n\n"
                f"**สถานะ:** `Active` (เปิดรับลงเวลา)\n"
                f"• เริ่มรอบเมื่อ: `<t:{int(now_dt.timestamp())}:T>`\n"
                f"• รหัส OTP: ดูได้ที่ห้อง <#{config.get('OPERATION_OTP_CHANNEL')}>\n"
            ),
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=now_dt
        )
        if stop_time:
            embed.description += f"• กำหนดเวลาปิดรอบ: `{stop_time}` น.\n"
        embed.set_footer(text="What Universal Time Tracker")

        status_msg = await interaction.channel.send(embed=embed)

        # ส่งปุ่ม
        btn_view = OperationView()
        sent_btn_msg = await interaction.channel.send(
            content="-# กดปุ่มด้านล่างเพื่อลงเวลาทำงาน หรือตรวจสอบสถานะของคุณ",
            view=btn_view
        )

        # สร้าง OTP แรก
        otp_min = int(config.get("OTP_EXPIRY_MINUTES", 20))
        first_otp = generate_otp()
        first_exp = now_dt + timedelta(minutes=otp_min)

        op_status = {
            "start_time": now_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "status_message_id": status_msg.id,
            "status_channel_id": interaction.channel.id,
            "button_messages": [{"channel_id": sent_btn_msg.channel.id, "message_id": sent_btn_msg.id}],
            "recent_sessions": [],
            "otp_verified_users": {},
            "otp": first_otp,
            "otp_generated_at": now_dt.isoformat(),
            "otp_expires_at": first_exp.isoformat()
        }
        if stop_time:
            op_status["temp_auto_stop"] = stop_time

        # ส่ง OTP เข้าห้อง
        otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
        otp_sent_ok = False
        if otp_ch_id:
            ch = await self.get_channel(otp_ch_id)
            if ch:
                try:
                    otp_embed = discord.Embed(
                        title="OTP PASSCODE — TIME TRACKER",
                        description=(
                            f"รหัสสำหรับใช้ Check-in ประจำรอบนี้:\n\n"
                            f"# `{first_otp}`\n\n"
                            f"• เปลี่ยนรหัสทุก: `{otp_min} นาที`\n"
                            f"• หมดอายุใน: <t:{int(first_exp.timestamp())}:R>\n\n"
                            f"-# กรุณานำรหัส 4 หลักไปกรอกในปุ่ม Check-in เพื่อเริ่มบันทึกเวลา"
                        ),
                        color=discord.Color.from_rgb(88, 101, 242),
                        timestamp=now_dt
                    )
                    otp_embed.set_footer(text="What Universal OTP Service")
                    otp_msg = await ch.send(embed=otp_embed)
                    op_status["otp_message_id"] = otp_msg.id
                    otp_sent_ok = True
                    logger.info(f"ส่งรหัส OTP ไปยังห้อง {ch.name} ({otp_ch_id}) สำเร็จ")
                except Exception as e:
                    logger.error(f"ไม่สามารถส่ง OTP ไปยังห้อง {otp_ch_id}: {e}")
            else:
                logger.error(f"ไม่พบบัญชีห้อง OTP ID: {otp_ch_id}")
        else:
            logger.warning("ยังไม่ได้ตั้งค่า OPERATION_OTP_CHANNEL ใน config.json")

        await save_operation_status(op_status)

        desc_log = f"ผู้เปิดรอบ: **{interaction.user.display_name}** (<@{interaction.user.id}>)\nเวลาเริ่ม: `{now_dt.strftime('%H:%M:%S')}`"
        if stop_time:
            desc_log += f"\nเวลาปิดรอบที่ตั้งไว้: `{stop_time}` น."

        await self.send_assistant_log(
            "SESSION OPENED",
            desc_log,
            color=discord.Color.from_rgb(88, 101, 242)
        )

        status_reply = "✅ เปิดรอบบันทึกเวลาสำเร็จแล้ว!"
        if stop_time:
            status_reply += f"\n⏰ ตั้งเวลาปิดรอบอัตโนมัติไว้ที่: `{stop_time}` น."
        if otp_ch_id and not otp_sent_ok:
            status_reply += f"\n⚠️ **แจ้งเตือน**: ไม่สามารถส่ง OTP ไปยังห้อง <#{otp_ch_id}> ได้ (กรุณาตรวจดูสิทธิ์บอทในช่องดังกล่าว)"
        await interaction.followup.send(status_reply, ephemeral=True)

    @app_commands.command(name="operation_set_stop", description="ตั้งหรือเปลี่ยนเวลาปิดรอบอัตโนมัติ (เฉพาะรอบปัจจุบัน)")
    @app_commands.describe(time="เวลาที่ต้องการปิดรอบ (HH:MM เช่น 18:00 หรือ 03:00)")
    async def operation_set_stop(self, interaction: discord.Interaction, time: str):
        if not self.is_assistant_or_above(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        time = time.strip()
        if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", time):
            return await interaction.response.send_message("❌ รูปแบบเวลาไม่ถูกต้อง (ใช้ HH:MM เช่น 18:00 หรือ 03:00)", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        op_status = await load_operation_status()
        if not op_status.get("status_message_id"):
            return await interaction.followup.send("⚠️ ขณะนี้ไม่มีรอบการปฏิบัติงานที่เปิดอยู่", ephemeral=True)

        op_status["temp_auto_stop"] = time
        await save_operation_status(op_status)

        await self.send_assistant_log(
            "⏰ ตั้งเวลาปิดรอบอัตโนมัติ",
            f"ผู้ตั้ง: **{interaction.user.display_name}** (<@{interaction.user.id}>)\nเวลาปิด: `{time}` น.",
            color=discord.Color.blue()
        )
        await interaction.followup.send(f"✅ กำหนดเวลาปิดรอบอัตโนมัติเป็นเวลา `{time}` น. เรียบร้อยแล้ว!", ephemeral=True)

    @app_commands.command(name="operation_stop", description="ยุติรอบปฏิบัติการของทุกคนและสรุปผล")
    async def operation_stop(self, interaction: discord.Interaction):
        if not self.is_assistant_or_above(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        await self.execute_stop(interaction=interaction, source="assistant_stop")

    @app_commands.command(name="force_stop", description="หยุดเวลาของผู้ใช้ที่ระบุเป็นรายบุคคล")
    @app_commands.describe(member="แท็กผู้ใช้ที่ต้องการให้ออกเวร")
    async def force_stop(self, interaction: discord.Interaction, member: discord.Member):
        if not self.is_assistant_or_above(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        op_status = await load_operation_status()
        uid = str(member.id)

        if uid not in op_status or "start_time" not in op_status[uid]:
            return await interaction.followup.send(f"⚠️ **{member.display_name}** ไม่ได้ลงเวลาอยู่ในขณะนี้", ephemeral=True)

        now_dt = datetime.now(config.timezone)
        start_str = op_status[uid].get("start_time")
        try:
            st_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
            duration = int((now_dt - st_dt).total_seconds())
        except Exception:
            duration = 0

        pname = clean_name(op_status[uid].get("player_name", member.display_name))
        await save_operation_session_data(
            user_id=uid,
            player_name=pname,
            start=start_str,
            end=now_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
            duration=duration,
            source=f"force_stop_by:{interaction.user.id}"
        )

        del op_status[uid]
        await save_operation_status(op_status)

        # ส่ง Personal Log สำหรับ Force Stop
        clean_start = start_str.split(".")[0] if "." in start_str else start_str
        await self.send_personal_log(
            user_id=uid,
            player_name=pname,
            start_time=clean_start,
            end_time=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            duration=duration,
            source=f"force_stop_by:{interaction.user.id}"
        )

        await self.send_assistant_log(
            "FORCE STOP ISSUED",
            f"ผู้สั่ง: **{interaction.user.display_name}**\nผู้ถูกสั่ง: **{member.display_name}** (<@{member.id}>)\nรวมเวลา: `{format_duration(duration)}`",
            color=discord.Color.from_rgb(245, 158, 11)
        )
        await interaction.followup.send(f"✅ บันทึกให้ **{member.display_name}** Check-out เรียบร้อยแล้ว (รวมเวลา: `{format_duration(duration)}`)", ephemeral=True)

    @app_commands.command(name="operation_get_daily_data", description="ดึงรายงานการทำงานรายวัน และส่งออกเป็น Excel")
    @app_commands.describe(date="วันที่ (DD/MM/YYYY)", min_hours="กรองผู้ที่ทำงานรวม >= ชั่วโมงนี้ (เช่น 1)")
    async def operation_get_daily_data(self, interaction: discord.Interaction, date: str, min_hours: float = 0.0):
        if not self.is_assistant_or_above(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            query_date = datetime.strptime(date.strip(), "%d/%m/%Y")
        except ValueError:
            return await interaction.followup.send("❌ รูปแบบวันที่ไม่ถูกต้อง (ใช้ DD/MM/YYYY)", ephemeral=True)

        daily_data = await load_operation_daily_data(query_date)
        if not daily_data:
            return await interaction.followup.send(f"ℹ️ ไม่พบข้อมูลการทำงานสำหรับวันที่ `{date}`", ephemeral=True)

        wb = Workbook()
        ws = wb.active
        ws.title = "Daily Summary"
        ws.append(["Discord ID", "Player Name", "Start Time", "End Time", "Duration (Sec)", "Duration (Formatted)", "Source"])

        total_users = 0
        total_time_sec = 0

        for uid, udata in daily_data.items():
            user_total_sec = sum(int(s.get("duration_seconds", 0)) for s in udata.get("sessions", []))
            if (user_total_sec / 3600) < min_hours:
                continue

            total_users += 1
            total_time_sec += user_total_sec
            for s in udata.get("sessions", []):
                dur = int(s.get("duration_seconds", 0))
                ws.append([
                    uid,
                    udata.get("player_name", ""),
                    s.get("start", ""),
                    s.get("end", ""),
                    dur,
                    format_duration(dur),
                    s.get("source", "")
                ])

        excel_buf = io.BytesIO()
        wb.save(excel_buf)
        excel_buf.seek(0)

        embed = discord.Embed(
            title=f"📊 รายงานการทำงานประจำวันที่ {date}",
            color=discord.Color.blue()
        )
        embed.add_field(name="จำนวนกำลังพล", value=f"{total_users} นาย", inline=True)
        embed.add_field(name="เวลารวมทั้งหมด", value=format_duration(total_time_sec), inline=True)

        file = discord.File(excel_buf, filename=f"operation_summary_{query_date.strftime('%Y%m%d')}.xlsx")
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @app_commands.command(name="operation_backup", description="สำรองข้อมูลโฟลเดอร์ data เป็นไฟล์ ZIP")
    async def operation_backup(self, interaction: discord.Interaction):
        if not config.is_owner(interaction.user.id):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้ (สงวนสิทธิ์เฉพาะเจ้าของบอทเท่านั้น)", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
            tmp_path = tmp.name
            tmp.close()

            zip_base = tmp_path[:-4]
            await asyncio.to_thread(shutil.make_archive, zip_base, 'zip', DATA_DIR)

            with open(tmp_path, "rb") as f:
                file = discord.File(f, filename=f"universal_data_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
                await interaction.followup.send("📦 สำรองข้อมูลโฟลเดอร์ data สำเร็จ:", file=file, ephemeral=True)

            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception as e:
            await interaction.followup.send(f"❌ สำรองข้อมูลล้มเหลว: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    cog = OperationsCog(bot)
    await bot.add_cog(cog)
    bot.add_view(OperationView())
