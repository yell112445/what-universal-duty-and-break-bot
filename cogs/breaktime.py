import os
import aiohttp
from datetime import datetime, date, timedelta
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput, View, Button

from config_manager import config
from services.storage_service import read_leave_db, write_leave_db, AUTHORIZED_USERS_DIR
from services import leave_service

TYPE_MAP = {
    "GENERAL": "ลางานทั่วไป",
    "L_3_4": "ลา 4/3 วัน (ย่อย)",
    "L_7": "ลาพักร้อน 7 วัน (เหมา)",
    "BYPASS": "ลา (Bypass/Admin)"
}

STATUS_MAP = {
    "ON_LEAVE": "กำลังลา",
    "EXPIRED_PENDING_RETURN": "ครบกำหนด",
    "COMPLETED": "เสร็จสิ้น",
    "CANCELLED": "ยกเลิกแล้ว"
}

def clean_name(name: str) -> str:
    if not name:
        return "Unknown"
    return name.replace("\r", " ").replace("\n", " ").strip()

def _build_truncated_field(lines: list[str], empty_message: str, limit: int = 1024) -> str:
    if not lines:
        return empty_message
    value = ""
    count = 0
    buffer = 50
    for line in lines:
        if len(value) + len(line) + 1 > (limit - buffer):
            break
        value += line + "\n"
        count += 1
    remaining = len(lines) - count
    if remaining > 0:
        value += f"...(และอีก {remaining} รายการ)"
    return value.strip()

class CleanLeaveRequestModal(Modal):
    def __init__(self, cog: 'BreaktimeCog'):
        super().__init__(title="ยื่นคำขอลางาน")
        self.cog = cog
        self.start_date_input = TextInput(
            label="วันเริ่มลา (Start Date)",
            placeholder="เช่น วันนี้ หรือ 29/10/2026",
            style=discord.TextStyle.short,
            required=True,
            max_length=20
        )
        self.days_input = TextInput(
            label="จำนวนวันที่ต้องการลา (วัน) [สูงสุด 7 วัน]",
            placeholder="ระบุตัวเลข 1 ถึง 7 วัน",
            style=discord.TextStyle.short,
            required=True,
            max_length=1
        )
        self.reason_input = TextInput(
            label="เหตุผลการลา",
            placeholder="เช่น ติดธุระส่วนตัว / ไปต่างจังหวัด",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=200
        )
        self.add_item(self.start_date_input)
        self.add_item(self.days_input)
        self.add_item(self.reason_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        date_str = self.start_date_input.value.strip().lower()
        now_dt = datetime.now(config.timezone)
        today = now_dt.date()

        # แปลงวันที่เริ่มลา
        if date_str in ["วันนี้", "today"]:
            req_start_date = today
        else:
            try:
                req_start_date = datetime.strptime(date_str, "%d/%m/%Y").date()
            except ValueError:
                return await interaction.followup.send("❌ รูปแบบวันที่ไม่ถูกต้อง กรุณาใช้ `วันนี้` หรือ `DD/MM/YYYY` (เช่น 29/10/2026)", ephemeral=True)

        if req_start_date < today:
            return await interaction.followup.send("❌ ไม่สามารถยื่นลาย้อนหลังได้ครับ", ephemeral=True)

        # แปลงจำนวนวัน (สูงสุดไม่เกิน 7 วัน)
        try:
            days = int(self.days_input.value.strip())
            if days <= 0 or days > 7:
                return await interaction.followup.send("❌ สามารถลาได้สูงสุดไม่เกิน 7 วันต่อครั้ง (กรอก 1 - 7 วัน)", ephemeral=True)
        except ValueError:
            return await interaction.followup.send("❌ กรุณากรอกจำนวนวันเป็นตัวเลขจำนวนเต็ม 1 ถึง 7", ephemeral=True)

        req_end_date = req_start_date + timedelta(days=days - 1)
        reason = self.reason_input.value.strip()

        all_data = await read_leave_db()
        records = all_data.get("leave_records", [])
        user_id = str(interaction.user.id)
        max_quota = int(config.get("MAX_LEAVE_PER_DAY") or config.get("MAX_LEAVES_SAME_DAY") or 5)

        # ตรวจสอบว่ามีลาซ้ำในช่วงเวลาเดียวกันหรือไม่
        for r in records:
            if r.get("user_id") == user_id and r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
                try:
                    r_st = date.fromisoformat(r["start_date"])
                    r_en = date.fromisoformat(r["end_date"])
                    if not (req_end_date < r_st or req_start_date > r_en):
                        return await interaction.followup.send("⚠️ คุณมีรายการลาในช่วงเวลาดังกล่าวอยู่แล้วครับ", ephemeral=True)
                except Exception:
                    pass

        # ตรวจสอบโควตาจำนวนคนลาในแต่ละวัน
        curr_d = req_start_date
        while curr_d <= req_end_date:
            cnt = 0
            for r in records:
                if r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
                    try:
                        r_st = date.fromisoformat(r["start_date"])
                        r_en = date.fromisoformat(r["end_date"])
                        if r_st <= curr_d <= r_en:
                            cnt += 1
                    except Exception:
                        pass
            if cnt >= max_quota:
                return await interaction.followup.send(
                    f"❌ ไม่สามารถขอลางานได้ เนื่องจากวันที่ `{curr_d.strftime('%d/%m/%Y')}` มีคนลาเต็มโควตาแล้ว ({cnt}/{max_quota} คน)",
                    ephemeral=True
                )
            curr_d += timedelta(days=1)

        # บันทึกลงฐานข้อมูล
        new_record = {
            "user_id": user_id,
            "user_name": clean_name(interaction.user.display_name),
            "leave_type": "GENERAL",
            "start_date": req_start_date.isoformat(),
            "end_date": req_end_date.isoformat(),
            "duration_days": days,
            "reason": reason,
            "status": "ON_LEAVE",
            "created_at": now_dt.isoformat()
        }
        records.append(new_record)
        all_data["leave_records"] = records
        await write_leave_db(all_data)

        # อัปเดตบอร์ดรวมทันที
        if self.cog:
            await self.cog.update_leave_board()

        start_ts = int(datetime.combine(req_start_date, datetime.min.time(), tzinfo=config.timezone).timestamp())
        end_ts = int(datetime.combine(req_end_date, datetime.min.time(), tzinfo=config.timezone).timestamp())

        # ส่ง Audit Log เข้าห้อง LOG_CHANNEL_ID
        log_ch_id = config.get("LOG_CHANNEL_ID")
        if log_ch_id:
            log_ch = interaction.client.get_channel(int(log_ch_id))
            if log_ch:
                leave_audit = discord.Embed(
                    title="AUDIT LOG — LEAVE REQUEST FILED",
                    color=discord.Color.from_rgb(47, 49, 54),
                    timestamp=now_dt
                )
                leave_audit.add_field(name="User", value=f"{interaction.user.display_name} ({interaction.user.mention})", inline=True)
                leave_audit.add_field(name="Duration", value=f"`{days} วัน`", inline=True)
                leave_audit.add_field(name="Period", value=f"<t:{start_ts}:D> — <t:{end_ts}:D>", inline=False)
                leave_audit.add_field(name="Reason", value=f"`{reason}`", inline=False)
                leave_audit.set_footer(text="Leave Registry Audit")
                try:
                    await log_ch.send(embed=leave_audit)
                except Exception:
                    pass

        await interaction.followup.send(
            f"**ยื่นคำขอลางานเรียบร้อยแล้ว**\n"
            f"• วันที่: <t:{start_ts}:D> — <t:{end_ts}:D> (`{days} วัน`)\n"
            f"• เหตุผล: `{reason}`\n\n"
            f"-# ระบบบันทึกข้อมูลและอัปเดตลงบอร์ดเรียบร้อยแล้วครับ",
            ephemeral=True
        )

class CleanReturnToDutyModal(Modal):
    def __init__(self, cog: 'BreaktimeCog'):
        super().__init__(title="ยืนยันการกลับเข้าเวร")
        self.cog = cog
        self.officer_input = TextInput(
            label="ชื่อผู้รับเข้าเวร (Confirming Officer)",
            placeholder="เช่น ผกก.สมชาย / ชื่อในเกมของเจ้าหน้าที่ผู้รับเข้าเวร",
            style=discord.TextStyle.short,
            required=True,
            min_length=2,
            max_length=50
        )
        self.add_item(self.officer_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        officer_name = self.officer_input.value.strip()

        is_success, reason = await leave_service.process_return_to_duty(user_id, officer_name)
        if is_success:
            now_ts = int(datetime.now().timestamp())
            embed = discord.Embed(
                title="✅ บันทึกการกลับเข้าเวรสำเร็จ",
                description=(
                    f"ยินดีต้อนรับกลับเข้าสู่การปฏิบัติหน้าที่!\n\n"
                    f"• **เจ้าหน้าที่:** {interaction.user.mention}\n"
                    f"• **ผู้รับเข้าเวร:** `{officer_name}`\n"
                    f"• **เวลาที่กลับเข้าเวร:** <t:{now_ts}:F> (<t:{now_ts}:R>)\n\n"
                    f"-# สถานะการลาของคุณถูกปรับเป็นเสร็จสิ้น (COMPLETED) และปลดสถานะลาออกจากระบบเรียบร้อยแล้ว"
                ),
                color=discord.Color.from_rgb(46, 204, 113)
            )
            embed.set_footer(text="What Universal Leave System")
            await interaction.followup.send(embed=embed, ephemeral=True)
            await self.cog.update_leave_board()
        else:
            await interaction.followup.send(f"❌ **ล้มเหลว:** {reason}", ephemeral=True)

class CleanLeaveBoardView(View):
    def __init__(self, cog: Optional['BreaktimeCog'] = None):
        super().__init__(timeout=None)
        self.cog = cog

    # ปุ่ม 1: ฟ้า (Primary) - ยื่นลา
    @discord.ui.button(label="ยื่นลา", style=discord.ButtonStyle.primary, custom_id="breaktime_request_leave", row=0)
    async def request_button(self, interaction: discord.Interaction, button: Button):
        cog = self.cog or interaction.client.get_cog("BreaktimeCog")
        await interaction.response.send_modal(CleanLeaveRequestModal(cog))

    # ปุ่ม 2: เทา (Secondary) - เช็คสิทธิ์
    @discord.ui.button(label="เช็คสิทธิ์", style=discord.ButtonStyle.secondary, custom_id="breaktime_check_quota", row=0)
    async def check_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        all_data = await read_leave_db()
        records = all_data.get("leave_records", [])
        user_id = str(interaction.user.id)
        today = datetime.now(config.timezone).date()
        max_quota = int(config.get("MAX_LEAVE_PER_DAY") or config.get("MAX_LEAVES_SAME_DAY") or 5)

        today_count = 0
        user_active_leave = None
        for r in records:
            if r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
                try:
                    r_st = date.fromisoformat(r["start_date"])
                    r_en = date.fromisoformat(r["end_date"])
                    if r_st <= today <= r_en:
                        today_count += 1
                        if r.get("user_id") == user_id:
                            user_active_leave = r
                except Exception:
                    pass

        embed = discord.Embed(
            title="สิทธิ์การลางานของคุณ",
            description=f"**ผู้ใช้งาน:** {interaction.user.mention}\n-# ตรวจสอบโควตาและสถานะการลา",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        if user_active_leave:
            end_d = user_active_leave.get("end_date", "-")
            rem = (date.fromisoformat(end_d) - today).days if end_d != "-" else 0
            embed.add_field(name="สถานะของคุณ", value=f"`กำลังลา` (สิ้นสุด `{end_d}` เหลือ {max(0, rem)} วัน)", inline=False)
        else:
            embed.add_field(name="สถานะของคุณ", value="`ปกติ` (สามารถยื่นลาได้)", inline=True)

        rem_quota = max(0, max_quota - today_count)
        embed.add_field(name="โควตาวันนี้", value=f"`{today_count} / {max_quota}` คน (ว่าง {rem_quota} ที่)", inline=True)
        embed.add_field(name="เพดานและเงื่อนไขการลา", value="• โควตาสูงสุด: วันละไม่เกิน `5` คน (ห้ามเกิน)\n• ระยะเวลาลา: สูงสุดไม่เกิน `7` วันต่อครั้ง", inline=False)
        embed.set_footer(text="What Universal Leave System")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ปุ่ม 3: เขียว (Success) - กลับเข้าเวร
    @discord.ui.button(label="กลับเข้าเวร", style=discord.ButtonStyle.success, custom_id="return_to_duty", row=0)
    async def return_duty_button(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        is_success, reason = await leave_service.perform_return_pre_check(user_id)
        if not is_success:
            return await interaction.response.send_message(f"❌ **ไม่สามารถกลับเข้าเวรได้**\n{reason}", ephemeral=True)

        cog = self.cog or interaction.client.get_cog("BreaktimeCog")
        await interaction.response.send_modal(CleanReturnToDutyModal(cog))

class LeaveTypeView(View):
    """View ดั้งเดิมสำหรับความเข้ากันได้ย้อนหลัง 100%"""
    def __init__(self, cog: Optional['BreaktimeCog'] = None):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="ยื่นคำขอลางาน", style=discord.ButtonStyle.primary, custom_id="leave_3_4")
    async def legacy_l3_4(self, interaction: discord.Interaction, button: Button):
        cog = self.cog or interaction.client.get_cog("BreaktimeCog")
        await interaction.response.send_modal(CleanLeaveRequestModal(cog))

    @discord.ui.button(label="เช็คสิทธิ์", style=discord.ButtonStyle.secondary, custom_id="leave_7")
    async def legacy_l7(self, interaction: discord.Interaction, button: Button):
        view = CleanLeaveBoardView(self.cog)
        await view.check_button.callback(view, interaction)

    @discord.ui.button(label="กลับเข้าเวร", style=discord.ButtonStyle.secondary, custom_id="legacy_return_to_duty")
    async def legacy_return(self, interaction: discord.Interaction, button: Button):
        user_id = str(interaction.user.id)
        is_success, reason = await leave_service.perform_return_pre_check(user_id)
        if not is_success:
            return await interaction.response.send_message(f"❌ **ไม่สามารถกลับเข้าเวรได้**\n{reason}", ephemeral=True)

        cog = self.cog or interaction.client.get_cog("BreaktimeCog")
        await interaction.response.send_modal(CleanReturnToDutyModal(cog))

class BreaktimeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_expired_leaves_loop.start()
        self.ping_heartbeat_loop.start()

    def cog_unload(self):
        self.check_expired_leaves_loop.cancel()
        self.ping_heartbeat_loop.cancel()

    async def update_leave_board(self):
        """อ่าน DB แล้วอัปเดตกระดาน Leave Board แบบ Clean รวมข้อความเดียวพร้อมปุ่ม"""
        all_data = await read_leave_db()
        board_message_id = all_data.get("leave_board_message_id")
        board_channel_id = config.get("LEAVE_BOARD_CHANNEL_ID")
        if not board_channel_id:
            return

        channel = self.bot.get_channel(int(board_channel_id))
        if not channel:
            return

        message = None
        if board_message_id:
            try:
                message = await channel.fetch_message(int(board_message_id))
            except (discord.NotFound, discord.HTTPException):
                message = None

        all_records = all_data.get("leave_records", [])
        today = datetime.now(config.timezone).date()
        today_iso = today.isoformat()
        max_quota = int(config.get("MAX_LEAVE_PER_DAY") or config.get("MAX_LEAVES_SAME_DAY") or 5)

        on_leave_list = []
        queued_list = []
        today_count = 0

        all_records.sort(key=lambda x: x.get("start_date", "9999-12-31"))

        for r in all_records:
            if r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
                uid = r.get("user_id")
                st = r.get("start_date", today_iso)
                en = r.get("end_date", today_iso)
                dur = r.get("duration_days", 1)

                try:
                    st_d = date.fromisoformat(st)
                    en_d = date.fromisoformat(en)
                except Exception:
                    st_d = today
                    en_d = today

                member = channel.guild.get_member(int(uid)) if channel.guild and str(uid).isdigit() else None
                user_name = member.display_name if member else f"User {uid}"
                name_display = f"{user_name} (<@{uid}>)"

                en_ts = int(datetime.combine(en_d, datetime.min.time(), tzinfo=config.timezone).timestamp())
                if st_d <= today <= en_d:
                    today_count += 1
                    rem = (en_d - today).days
                    on_leave_list.append(f"• {name_display} — สิ้นสุด <t:{en_ts}:D> (เหลือ {max(0, rem)} วัน)")
                elif st_d > today:
                    st_ts = int(datetime.combine(st_d, datetime.min.time(), tzinfo=config.timezone).timestamp())
                    queued_list.append(f"• {name_display} — ลา {dur} วัน (<t:{st_ts}:D> — <t:{en_ts}:D>)")

        c_slate = discord.Color.from_rgb(47, 49, 54)
        embed = discord.Embed(
            title="WHAT UNIVERSAL — LEAVE BOARD",
            description="-# รายงานสถานะวันลาและตรวจสอบโควตาประจำวัน",
            color=c_slate
        )
        rem_slots = max(0, max_quota - today_count)
        embed.add_field(
            name="โควตาวันนี้",
            value=f"ใช้ไป `{today_count} / {max_quota}` คน (ว่างอีก `{rem_slots}` ที่)",
            inline=False
        )
        embed.add_field(
            name="คนที่กำลังลาอยู่",
            value="\n".join(on_leave_list[:20]) if on_leave_list else "*— ไม่มีรายชื่อในขณะนี้ —*",
            inline=False
        )
        embed.add_field(
            name="คนที่ลาล่วงหน้า",
            value="\n".join(queued_list[:20]) if queued_list else "*— ไม่มีคิวล่วงหน้า —*",
            inline=False
        )
        embed.set_footer(text="ตัดรอบวันลาทุก 03:00 น.")

        view = CleanLeaveBoardView(self)
        try:
            if message:
                await message.edit(content="", embed=embed, view=view)
            else:
                new_msg = await channel.send(embed=embed, view=view)
                all_data["leave_board_message_id"] = new_msg.id
                await write_leave_db(all_data)
        except Exception as e:
            print(f"[BreaktimeCog] Error updating leave board: {e}")

    async def ensure_panel_message(self):
        """อัปเดตหรือซ่อมแซมกระดาน Leave Board แบบรวมข้อความเดียว"""
        await self.update_leave_board()

    @tasks.loop(minutes=30)
    async def check_expired_leaves_loop(self):
        """ตรวจสอบผู้ที่ครบกำหนดวันลาแล้วยังไม่ได้กดกลับเข้าเวร"""
        try:
            expired_users = await leave_service.check_and_expire_leaves()
            if expired_users:
                await self.update_leave_board()
                contact_admin_id = config.get("EXPIRED_CONTACT_ADMIN_ID", "331348756773732352")
                for u in expired_users:
                    try:
                        user = await self.bot.fetch_user(int(u["user_id"]))
                        if user:
                            dm_embed = discord.Embed(
                                title="NOTICE: LEAVE PERIOD CONCLUDED",
                                description=(
                                    f"การลาของคุณสิ้นสุดลงเมื่อวันที่ **{u['end_date']}**\n\n"
                                    f"-# หากมีข้อสงสัยโปรดติดต่อ <@{contact_admin_id}>"
                                ),
                                color=discord.Color.from_rgb(245, 158, 11)
                            )
                            await user.send(embed=dm_embed)
                    except Exception as e:
                        print(f"[BreaktimeCog] DM to {u['user_id']} failed: {e}")
        except Exception as e:
            print(f"[BreaktimeCog] check_expired_leaves_loop error: {e}")

    @tasks.loop(minutes=10)
    async def ping_heartbeat_loop(self):
        """ส่ง ping ไปยัง Webhook มอนิเตอร์ (เช่น Healthcheck.io)"""
        url = config.heartbeat_webhook_url
        if not url:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    pass
        except Exception as e:
            pass

    @check_expired_leaves_loop.before_loop
    @ping_heartbeat_loop.before_loop
    async def before_tasks(self):
        await self.bot.wait_until_ready()

    # --- Slash Commands ---
    @app_commands.command(name="leave_setup", description="[Admin/Owner] ติดตั้งและส่งแผง Leave Board ลงในห้องที่กำหนด")
    @app_commands.describe(channel="เลือกห้องที่ต้องการให้แสดง Leave Board (เว้นว่างเพื่อใช้ห้องปัจจุบัน)")
    async def leave_setup_command(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not (config.is_admin(interaction.user.id) or config.is_owner(interaction.user.id)):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        target_ch = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)

        await config.set("LEAVE_BOARD_CHANNEL_ID", target_ch.id)
        await config.set("LEAVE_PANEL_CHANNEL_ID", target_ch.id)

        all_data = await read_leave_db()
        all_data["leave_board_message_id"] = None
        await write_leave_db(all_data)

        await self.update_leave_board()
        await interaction.followup.send(f"✅ ติดตั้งแผง Leave Board สำเร็จที่ห้อง {target_ch.mention} เรียบร้อยแล้ว!", ephemeral=True)

    @app_commands.command(name="leave", description="เริ่มต้นกระบวนการขอลางาน")
    async def leave_command(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CleanLeaveRequestModal(self))

    @app_commands.command(name="cancel_leave", description="ยกเลิกการลาของตนเอง หรือ [Admin] ยกเลิกการลาของใครก็ได้")
    @app_commands.describe(member="แท็กผู้ที่ต้องการยกเลิกการลา (เฉพาะ Admin)")
    async def cancel_leave_command(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        await interaction.response.defer(ephemeral=True)
        is_admin = config.is_admin(interaction.user.id)
        target_id = str(member.id if member else interaction.user.id)

        is_success, reason = await leave_service.cancel_leave_request(
            user_id_requesting=str(interaction.user.id),
            user_id_to_cancel=target_id,
            is_admin=is_admin
        )
        if is_success:
            await interaction.followup.send(f"✅ {reason}", ephemeral=True)
            await self.update_leave_board()
        else:
            await interaction.followup.send(f"❌ {reason}", ephemeral=True)

    @app_commands.command(name="force_leave", description="[Admin] บังคับลา (Bypass) ข้ามทุกกฎเกณฑ์")
    @app_commands.describe(
        member="แท็กผู้ที่ต้องการให้ลา",
        start_date="วันที่เริ่มต้น (DD/MM/YYYY)",
        end_date="วันที่สิ้นสุด (DD/MM/YYYY)"
    )
    async def force_leave_command(self, interaction: discord.Interaction, member: discord.Member, start_date: str, end_date: str):
        if not config.is_admin(interaction.user.id):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            start_dt = datetime.strptime(start_date.strip(), "%d/%m/%Y").date()
            end_dt = datetime.strptime(end_date.strip(), "%d/%m/%Y").date()
        except ValueError:
            return await interaction.followup.send("❌ รูปแบบวันที่ไม่ถูกต้อง (ใช้ DD/MM/YYYY)", ephemeral=True)

        if end_dt < start_dt:
            return await interaction.followup.send("❌ วันที่สิ้นสุดต้องอยู่หลังวันที่เริ่มต้น", ephemeral=True)

        is_success, reason = await leave_service.process_bypass_leave(
            admin_id=str(interaction.user.id),
            user_id=str(member.id),
            request_start_date=start_dt,
            request_end_date=end_dt
        )
        if is_success:
            await interaction.followup.send(f"✅ {reason}", ephemeral=True)
            await self.update_leave_board()
        else:
            await interaction.followup.send(f"❌ {reason}", ephemeral=True)



async def setup(bot: commands.Bot):
    cog = BreaktimeCog(bot)
    await bot.add_cog(cog)
    bot.add_view(CleanLeaveBoardView(cog))
    bot.add_view(LeaveTypeView(cog))
