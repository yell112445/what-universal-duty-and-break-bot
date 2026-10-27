import io
import os
import random
import shutil
import tempfile
import asyncio
from datetime import datetime, timedelta, time
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
        super().__init__(title="กรอกรหัส OTP (4 หลัก)")
        self.opener_id = opener_id
        self.otp_field = TextInput(
            label="รหัส OTP 4 หลัก",
            style=discord.TextStyle.short,
            placeholder="ตัวเลข 4 หลัก",
            min_length=4,
            max_length=4,
            required=True
        )
        self.add_item(self.otp_field)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.opener_id:
            return await interaction.response.send_message("❌ Modal นี้ไม่ใช่ของคุณ", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        op_status = await load_operation_status()
        if not op_status.get("status_message_id"):
            return await interaction.followup.send("ขณะนี้ไม่มีรอบการทำงานที่เปิดอยู่", ephemeral=True)

        current_otp = op_status.get("otp")
        if not current_otp:
            return await interaction.followup.send("ไม่มี OTP ให้ตรวจสอบ กรุณาลองอีกครั้ง", ephemeral=True)

        expires_iso = op_status.get("otp_expires_at")
        if expires_iso:
            try:
                exp_dt = datetime.fromisoformat(expires_iso)
                if datetime.now(config.timezone) > exp_dt:
                    return await interaction.followup.send("❌ OTP หมดอายุแล้ว โปรดรอ OTP ถัดไป", ephemeral=True)
            except Exception:
                pass

        if self.otp_field.value.strip() != current_otp:
            return await interaction.followup.send("❌ รหัส OTP ไม่ถูกต้อง กรุณาดูในห้อง OTP", ephemeral=True)

        # บันทึกสถานะผู้ที่ยืนยัน OTP แล้ว
        user_id = str(interaction.user.id)
        verified = op_status.get("otp_verified_users", {}) or {}
        verified[user_id] = datetime.now(config.timezone).isoformat()
        op_status["otp_verified_users"] = verified

        # บันทึกเริ่มการทำงานทันที
        now_dt = datetime.now(config.timezone)
        op_status[user_id] = {
            "player_name": clean_name(interaction.user.display_name),
            "start_time": now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        }
        await save_operation_status(op_status)

        await interaction.followup.send(
            f"✅ ยืนยัน OTP สำเร็จ! เริ่มบันทึกเวลาทำงานแล้วตั้งแต่ `{now_dt.strftime('%H:%M:%S')}`",
            ephemeral=True
        )

class OperationView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ลงเวลาปฏิบัติการ (Check in / Check out)", style=discord.ButtonStyle.primary, custom_id="operation_stamp_button")
    async def stamp_button(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        user_id = str(user.id)
        op_status = await load_operation_status()

        if not op_status.get("status_message_id"):
            return await interaction.response.send_message(
                "ℹ️ ขณะนี้ยังไม่มีรอบการทำงานที่เปิดอยู่ กรุณารอ Assistant ใช้คำสั่ง `/operation_start`",
                ephemeral=True
            )

        # ตรวจสอบว่าถ้ายังไม่ได้เริ่มงาน และยังไม่ verify OTP -> แสดง Modal
        if user_id not in op_status:
            verified_users = op_status.get("otp_verified_users", {}) or {}
            if user_id not in verified_users:
                return await interaction.response.send_modal(OTPModal(user.id))

        await interaction.response.defer(ephemeral=True, thinking=True)

        # Anti-spam cooldown
        cooldowns = op_status.get("stamp_cooldowns", {}) or {}
        now_dt = datetime.now(config.timezone)
        last_stamp_iso = cooldowns.get(user_id)
        button_cd = int(config.get("BUTTON_COOLDOWN_SECONDS", 10))

        if last_stamp_iso:
            try:
                last_dt = datetime.fromisoformat(last_stamp_iso)
                diff = (now_dt - last_dt).total_seconds()
                if diff < button_cd:
                    wait_sec = int(button_cd - diff)
                    return await interaction.followup.send(f"⏳ กรุณารออีก {wait_sec} วินาทีก่อนกดอีกครั้ง", ephemeral=True)
            except Exception:
                pass

        cooldowns[user_id] = now_dt.isoformat()
        op_status["stamp_cooldowns"] = cooldowns

        # สลับสถานะ: ถ้า active อยู่ -> ออกงาน (Check out)
        if user_id in op_status and isinstance(op_status[user_id], dict) and "start_time" in op_status[user_id]:
            start_time_str = op_status[user_id].get("start_time")
            try:
                start_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
            except Exception:
                start_dt = now_dt

            duration = int((now_dt - start_dt).total_seconds())
            pname = clean_name(op_status[user_id].get("player_name", user.display_name))

            # บันทึกลงไฟล์รายวัน
            await save_operation_session_data(
                user_id=user_id,
                player_name=pname,
                start=start_time_str,
                end=now_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
                duration=duration,
                source="user_checkout"
            )

            # เก็บใน recent_sessions แล้วลบ active
            op_status.setdefault("recent_sessions", []).append({
                "discord_id": user_id,
                "player_name": pname,
                "start_time": start_time_str,
                "end_time": now_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
                "duration_seconds": duration
            })
            del op_status[user_id]
            await save_operation_status(op_status)

            await interaction.followup.send(
                f"🛑 หยุดบันทึกเวลาทำงานเรียบร้อย!\n⏱️ รวมเวลาในรอบนี้: **{format_duration(duration)}**",
                ephemeral=True
            )

        else:
            # เริ่มงาน (Check in)
            op_status[user_id] = {
                "player_name": clean_name(user.display_name),
                "start_time": now_dt.strftime("%Y-%m-%d %H:%M:%S.%f")
            }
            await save_operation_status(op_status)

            await interaction.followup.send(
                f"🟢 เริ่มบันทึกเวลาทำงานแล้ว!\n⏰ เวลาเริ่ม: `{now_dt.strftime('%H:%M:%S')}`",
                ephemeral=True
            )

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

    async def send_assistant_log(self, title: str, description: str, color: discord.Color = discord.Color.blurple()):
        ch_id = config.get("OPERATION_LOG_ASSISTANT_CHANNEL_ID")
        if not ch_id:
            return
        ch = self.bot.get_channel(int(ch_id))
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
                print(f"[OperationsCog] Failed to send assistant log: {e}")

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
                    ch = self.bot.get_channel(int(otp_ch_id))
                    if ch:
                        embed = discord.Embed(
                            title="🔐 รหัส OTP ปฏิบัติการ",
                            description=f"รหัส OTP ใหม่: `{new_otp}`",
                            color=discord.Color.gold(),
                            timestamp=now_dt
                        )
                        embed.add_field(name="⏳ หมดอายุ", value=f"<t:{int(new_exp.timestamp())}:R>")
                        embed.set_footer(text=f"รหัสจะเปลี่ยนใหม่ทุก {otp_min} นาที")

                        old_msg_id = op_status.get("otp_message_id")
                        if old_msg_id:
                            try:
                                old_msg = await ch.fetch_message(int(old_msg_id))
                                await old_msg.edit(embed=embed)
                            except Exception:
                                new_msg = await ch.send(embed=embed)
                                op_status["otp_message_id"] = new_msg.id
                        else:
                            new_msg = await ch.send(embed=embed)
                            op_status["otp_message_id"] = new_msg.id

                await save_operation_status(op_status)
        except Exception as e:
            print(f"[OperationsCog] otp_rotate_loop error: {e}")

    @tasks.loop(seconds=15)
    async def refresh_status_message_loop(self):
        """อัปเดต Embed สถานะกำลังพลที่ปฏิบัติการอยู่แบบ Realtime"""
        try:
            op_status = await load_operation_status()
            status_msg_id = op_status.get("status_message_id")
            status_ch_id = op_status.get("status_channel_id")

            if not status_msg_id or not status_ch_id:
                return

            ch = self.bot.get_channel(int(status_ch_id))
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
                title="📋 สถานะกำลังพลปฏิบัติการ (Operations Board)",
                description=f"กำลังพลที่กำลังปฏิบัติหน้าที่อยู่: **{active_count} นาย**",
                color=discord.Color.green() if active_count > 0 else discord.Color.dark_grey(),
                timestamp=now_dt
            )

            for unit_name, members in unit_groups.items():
                if members:
                    embed.add_field(name=f"🛡️ {unit_name} ({len(members)})", value="\n".join(members[:20]), inline=False)

            embed.set_footer(text=f"อัปเดตอัตโนมัติทุก 15 วินาที | {config.get('BOT_VERSION')}")
            await msg.edit(embed=embed)
        except Exception as e:
            print(f"[OperationsCog] refresh_status_message_loop error: {e}")

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

            if now_time_str in auto_stop_times:
                print(f"[OperationsCog] Auto-stop triggered at {now_time_str}")
                await self.execute_stop(interaction=None, source="auto_stop")
        except Exception as e:
            print(f"[OperationsCog] check_auto_stop_loop error: {e}")

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

        # ลบข้อความปุ่มและ OTP
        button_msgs = op_status.get("button_messages", []) or []
        for bm in button_msgs:
            try:
                ch = self.bot.get_channel(int(bm["channel_id"]))
                if ch:
                    m = await ch.fetch_message(int(bm["message_id"]))
                    await m.delete()
            except Exception:
                pass

        otp_msg_id = op_status.get("otp_message_id")
        otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
        if otp_msg_id and otp_ch_id:
            try:
                ch = self.bot.get_channel(int(otp_ch_id))
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
                ch = self.bot.get_channel(int(status_ch_id))
                if ch:
                    msg = await ch.fetch_message(int(status_msg_id))
                    embed = discord.Embed(
                        title="🏁 สิ้นสุดรอบการปฏิบัติงาน (Operation Ended)",
                        description=f"รอบปฏิบัติการสิ้นสุดลงเมื่อ `{now_dt.strftime('%H:%M:%S')}`",
                        color=discord.Color.red(),
                        timestamp=now_dt
                    )
                    lines = [f"• <@{uid}> — `{format_duration(dur)}`" for uid, name, dur in summary_records]
                    embed.add_field(name="สรุปเวลาทำงานรอบนี้", value="\n".join(lines[:25]) if lines else "*(ไม่มีกำลังพลค้างในรอบนี้)*")
                    await msg.edit(embed=embed, view=None)
            except Exception as e:
                print(f"[OperationsCog] Failed to edit status message on stop: {e}")

        # เคลียร์สถานะ
        await save_operation_status({})

        caller = interaction.user.display_name if interaction else "ระบบหยุดอัตโนมัติ (Auto Stop)"
        await self.send_assistant_log(
            "🛑 สิ้นสุดรอบปฏิบัติการ",
            f"ผู้สั่งการ: **{caller}**\nเวลา: `{now_dt.strftime('%d/%m/%Y %H:%M:%S')}`",
            color=discord.Color.red()
        )

        if interaction:
            await interaction.followup.send("✅ ยุติรอบปฏิบัติการและสรุปผลเรียบร้อยแล้ว", ephemeral=True)

    # --- Slash Commands ---
    @app_commands.command(name="operation_start", description="เริ่มรอบปฏิบัติการใหม่ และสร้างปุ่มลงเวลา")
    async def operation_start(self, interaction: discord.Interaction):
        if not self.is_assistant_or_above(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        op_status = await load_operation_status()
        if op_status.get("status_message_id"):
            return await interaction.followup.send("⚠️ มีรอบการทำงานเปิดอยู่แล้ว", ephemeral=True)

        now_dt = datetime.now(config.timezone)
        embed = discord.Embed(
            title="📋 บันทึกเวลาปฏิบัติงาน (กำลังเริ่มต้น...)",
            description="ระบบกำลังเตรียมแผงลงเวลาและ OTP",
            color=discord.Color.green(),
            timestamp=now_dt
        )
        status_msg = await interaction.channel.send(embed=embed)

        # ส่งปุ่ม
        btn_view = OperationView()
        sent_btn_msg = await interaction.channel.send(
            "**กดปุ่มด้านล่างเพื่อเริ่ม / หยุดบันทึกเวลาปฏิบัติการ**",
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

        # ส่ง OTP เข้าห้อง
        otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
        if otp_ch_id:
            ch = self.bot.get_channel(int(otp_ch_id))
            if ch:
                otp_embed = discord.Embed(
                    title="🔐 รหัส OTP ปฏิบัติการ",
                    description=f"รหัส OTP: `{first_otp}`",
                    color=discord.Color.gold(),
                    timestamp=now_dt
                )
                otp_embed.add_field(name="⏳ หมดอายุ", value=f"<t:{int(first_exp.timestamp())}:R>")
                otp_embed.set_footer(text=f"รหัสจะเปลี่ยนใหม่ทุก {otp_min} นาที")
                otp_msg = await ch.send(embed=otp_embed)
                op_status["otp_message_id"] = otp_msg.id

        await save_operation_status(op_status)
        await self.send_assistant_log(
            "🟢 เริ่มรอบปฏิบัติการใหม่",
            f"ผู้เปิดรอบ: **{interaction.user.display_name}** (<@{interaction.user.id}>)\nเวลา: `{now_dt.strftime('%H:%M:%S')}`",
            color=discord.Color.green()
        )
        await interaction.followup.send("✅ เปิดรอบการปฏิบัติงานสำเร็จแล้ว!", ephemeral=True)

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

        await self.send_assistant_log(
            "⚠️ สั่งให้ออกเวร (Force Stop)",
            f"ผู้สั่ง: **{interaction.user.display_name}**\nผู้ถูกสั่ง: **{member.display_name}** (<@{member.id}>)\nรวมเวลา: {format_duration(duration)}",
            color=discord.Color.orange()
        )
        await interaction.followup.send(f"✅ บันทึกให้ออกเวร **{member.display_name}** เรียบร้อยแล้ว (เวลา: {format_duration(duration)})", ephemeral=True)

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
        if not config.is_admin(interaction.user.id):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

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
