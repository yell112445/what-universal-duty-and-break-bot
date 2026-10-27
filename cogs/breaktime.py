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
    "L_3_4": "ลา 4/3 วัน (ย่อย)",
    "L_7": "ลาพักร้อน 7 วัน (เหมา)",
    "BYPASS": "ลา (Bypass/Admin)"
}

STATUS_MAP = {
    "ON_LEAVE": "🟢 กำลังลา",
    "EXPIRED_PENDING_RETURN": "🔴 หมดอายุ (รอกลับ)",
    "COMPLETED": "⚪ กลับเข้าเวรแล้ว",
    "CANCELLED": "❌ ยกเลิกแล้ว"
}

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

class ConfirmLateLeaveView(View):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value = None

    @discord.ui.button(label="ยืนยัน (ใช้สิทธิ์วันนี้)", style=discord.ButtonStyle.primary)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.stop()
        await interaction.response.edit_message(content="⏳ ยืนยันสำเร็จ กำลังดำเนินการต่อ...", view=None)

    @discord.ui.button(label="ยกเลิก (ไม่ลา)", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.stop()
        await interaction.response.edit_message(content="👍 การขอลายกเลิกแล้ว", view=None)

class LeaveRequestModal(Modal):
    def __init__(self, cog: 'BreaktimeCog', leave_type: str, duration_days: int):
        super().__init__(title=f"ขอลางาน: {TYPE_MAP.get(leave_type, leave_type)}")
        self.cog = cog
        self.leave_type = leave_type
        self.duration_days = duration_days

        self.start_date_input = TextInput(
            label="วันที่เริ่ม (DD/MM/YYYY)",
            placeholder="ตัวอย่าง: 20/11/2026",
            style=discord.TextStyle.short,
            required=True
        )
        self.add_item(self.start_date_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        start_date_str = self.start_date_input.value.strip()

        try:
            req_start_date = datetime.strptime(start_date_str, "%d/%m/%Y").date()
        except ValueError:
            return await interaction.followup.send("❌ **Format วันที่ผิด** กรุณาใช้ `DD/MM/YYYY` (เช่น 20/11/2026)", ephemeral=True)

        if req_start_date.year > 2100:
            return await interaction.followup.send(
                f"❌ **ปีไม่ถูกต้อง** กรุณาใช้ปี ค.ศ. เช่น 2026 *(คุณกรอก: {req_start_date.year})*",
                ephemeral=True
            )

        now = datetime.now(config.timezone)
        today = now.date()

        if req_start_date < today:
            return await interaction.followup.send("❌ **วันที่ไม่ถูกต้อง** ไม่อนุญาตให้เริ่มลาในวันที่ผ่านไปแล้ว", ephemeral=True)

        if req_start_date.month != today.month or req_start_date.year != today.year:
            return await interaction.followup.send(
                "❌ **ไม่สามารถจองล่วงหน้าข้ามเดือนได้** ระบบอนุญาตให้จองได้เฉพาะภายในเดือนปัจจุบันเท่านั้น",
                ephemeral=True
            )

        # แจ้งเตือนหากลาหลัง 18:00
        LATE_NIGHT_THRESHOLD_HOUR = 18
        if req_start_date == today and now.hour >= LATE_NIGHT_THRESHOLD_HOUR:
            midnight_today = datetime.combine(now.date(), datetime.min.time(), tzinfo=config.timezone)
            time_lost = now - midnight_today
            total_seconds = int(time_lost.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            time_lost_str = f"**{hours} ชั่วโมง {minutes} นาที**"

            confirmation_view = ConfirmLateLeaveView(timeout=60)
            embed = discord.Embed(
                title="⚠️ โปรดยืนยัน (ป้องกันการเสียสิทธิ์)",
                color=discord.Color.orange(),
                description=(
                    f"คุณกำลังลงลา 'วันนี้' ({req_start_date.strftime('%d/%m')}) ในเวลา {now.strftime('%H:%M')} น.\n\n"
                    f"หมายความว่าคุณจะ **เสียสิทธิ์ของวันนี้ไป** {time_lost_str} (จากเต็มวัน)\n\n"
                    f"**คุณแน่ใจหรือไม่ว่าต้องการดำเนินการต่อ?**\n"
                    f"*(หากกดยกเลิก กรุณากดลาใหม่อีกครั้งและเลือกวันพรุ่งนี้)*"
                )
            )
            await interaction.followup.send(embed=embed, view=confirmation_view, ephemeral=True)
            await confirmation_view.wait()

            if confirmation_view.value is None:
                return await interaction.followup.send("⌛ หมดเวลาตอบกลับ การขอลายกเลิก", ephemeral=True)
            elif confirmation_view.value is False:
                return

        user_id = str(interaction.user.id)
        is_success, reason = await leave_service.process_leave_request(
            user_id=user_id,
            request_start_date=req_start_date,
            duration_days=self.duration_days,
            leave_type=self.leave_type
        )

        if is_success:
            await interaction.followup.send(f"✅ **อนุมัติ** {reason}", ephemeral=True)
            await self.cog.update_leave_board()
        else:
            await interaction.followup.send(f"❌ **ไม่อนุมัติ** {reason}", ephemeral=True)

class ReturnToDutyModal(Modal):
    def __init__(self, cog: 'BreaktimeCog'):
        super().__init__(title="ยืนยันการกลับเข้าเวร")
        self.cog = cog
        self.confirming_officer_input = TextInput(
            label="ชื่อผู้รับเข้าเวร (Confirming Officer)",
            placeholder="กรอกชื่อในเกมของคนที่รับคุณกลับเข้าเวร",
            style=discord.TextStyle.short,
            required=True,
            min_length=3
        )
        self.add_item(self.confirming_officer_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        officer_name = self.confirming_officer_input.value.strip()
        user_id = str(interaction.user.id)
        is_success, reason = await leave_service.process_return_to_duty(user_id, officer_name)
        if is_success:
            await interaction.followup.send(f"✅ **บันทึกการกลับเข้าเวรสำเร็จ**\n{reason}", ephemeral=True)
            await self.cog.update_leave_board()
        else:
            await interaction.followup.send(f"❌ **ล้มเหลว**\n{reason}", ephemeral=True)

class LeaveTypeView(View):
    def __init__(self, cog: Optional['BreaktimeCog'] = None):
        super().__init__(timeout=None)
        self.cog = cog

    async def run_pre_checks(self, interaction: discord.Interaction, leave_type: str) -> tuple[bool, int]:
        user_id = str(interaction.user.id)
        user_role_ids = [role.id for role in interaction.user.roles]
        is_success, reason, duration_days = await leave_service.perform_pre_checks(user_id, user_role_ids, leave_type)
        if not is_success:
            await interaction.response.send_message(f"❌ **ไม่อนุญาตให้ลา**\n{reason}", ephemeral=True)
            return (False, 0)
        return (True, duration_days)

    @discord.ui.button(label="ลา 4/3 วัน (ย่อย)", style=discord.ButtonStyle.secondary, custom_id="leave_3_4")
    async def leave_3_4_callback(self, interaction: discord.Interaction, button: Button):
        is_success, duration_days = await self.run_pre_checks(interaction, "L_3_4")
        if not is_success:
            return
        await interaction.response.send_modal(LeaveRequestModal(self.cog, "L_3_4", duration_days))

    @discord.ui.button(label="ลาพักร้อน 7 วัน (เหมา)", style=discord.ButtonStyle.primary, custom_id="leave_7")
    async def leave_7_callback(self, interaction: discord.Interaction, button: Button):
        is_success, duration_days = await self.run_pre_checks(interaction, "L_7")
        if not is_success:
            return
        await interaction.response.send_modal(LeaveRequestModal(self.cog, "L_7", duration_days))

    @discord.ui.button(label="กลับเข้าเวร (Return to Duty)", style=discord.ButtonStyle.danger, custom_id="return_to_duty")
    async def return_to_duty_callback(self, interaction: discord.Interaction, button: Button):
        is_success, reason = await leave_service.perform_return_pre_check(str(interaction.user.id))
        if not is_success:
            return await interaction.response.send_message(f"❌ **ไม่สามารถกลับเข้าเวรได้**\n{reason}", ephemeral=True)
        await interaction.response.send_modal(ReturnToDutyModal(self.cog))

class BreaktimeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_expired_leaves_loop.start()
        self.ping_heartbeat_loop.start()

    def cog_unload(self):
        self.check_expired_leaves_loop.cancel()
        self.ping_heartbeat_loop.cancel()

    async def update_leave_board(self):
        """อ่าน DB แล้วอัปเดตหรือซ่อมแซมข้อความ Leave Board"""
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

        on_leave_list, queued_list, expired_list, history_list = [], [], [], []
        all_records = all_data.get("leave_records", [])
        user_data = all_data.get("user_data", {})
        today = datetime.now(config.timezone).date()
        today_str = today.isoformat()

        all_records.sort(key=lambda x: x.get("start_date", "9999-12-31"))

        for record in all_records:
            status = record.get("status")
            user_id = record.get("user_id")
            leave_type = record.get("leave_type", "N/A")
            leave_type_display = TYPE_MAP.get(leave_type, leave_type)
            start_date = record.get("start_date", "1970-01-01")
            end_date = record.get("end_date", "1970-01-01")

            try:
                start_date_fmt = date.fromisoformat(start_date).strftime("%d/%m/%Y")
                end_date_fmt = date.fromisoformat(end_date).strftime("%d/%m/%Y")
            except Exception:
                start_date_fmt = start_date
                end_date_fmt = end_date

            if status == "ON_LEAVE":
                if start_date <= today_str:
                    on_leave_list.append(f"<@{user_id}>\n   - (ประเภท: {leave_type_display}, ถึง: {end_date_fmt})")
                else:
                    queued_list.append(f"<@{user_id}>\n   - (ประเภท: {leave_type_display}, **เริ่ม: {start_date_fmt}** ถึง: {end_date_fmt})")
            elif status == "EXPIRED_PENDING_RETURN":
                days_overdue_str = ""
                try:
                    end_obj = date.fromisoformat(end_date)
                    overdue = (today - end_obj).days
                    days_overdue_str = f"เลยกำหนด {overdue} วัน" if overdue > 0 else "ครบกำหนด (วันนี้)"
                except Exception:
                    pass
                expired_list.append(f"<@{user_id}>\n   - (⚠️ **{days_overdue_str}** รอกดกลับเข้าเวร)")
            elif status == "COMPLETED":
                master_cooldown = leave_service.get_master_cooldown_date(all_records, user_data, user_id)
                if today < master_cooldown:
                    officer = record.get("confirming_officer", "N/A")
                    late_str = ""
                    return_ts = record.get("return_timestamp")
                    if return_ts:
                        try:
                            ret_dt = datetime.fromisoformat(return_ts)
                            end_obj = date.fromisoformat(end_date)
                            deadline_dt = datetime.combine(end_obj + timedelta(days=1), datetime.min.time(), tzinfo=config.timezone)
                            diff = ret_dt - deadline_dt
                            if diff.total_seconds() > 0:
                                total_sec = int(diff.total_seconds())
                                d, rem = divmod(total_sec, 86400)
                                h, rem = divmod(rem, 3600)
                                m, _ = divmod(rem, 60)
                                parts = []
                                if d > 0: parts.append(f"{d} วัน")
                                if h > 0: parts.append(f"{h} ชม.")
                                if m > 0: parts.append(f"{m} นาที")
                                if parts: late_str = f", เลยกำหนด {', '.join(parts)}"
                        except Exception:
                            pass
                    history_list.append(f"<@{user_id}>\n   - (กลับโดย: {officer}{late_str})\n   - [Cooldown ถึง: {master_cooldown.strftime('%d/%m/%Y')}]")

        COLOR_DUE = 0x992D22
        COLOR_UPCOMING = 0xCC8400
        COLOR_ON_LEAVE = 0x1F8B4C
        COLOR_HISTORY = 0x546E7A

        dynamic_color = COLOR_HISTORY
        if expired_list:
            dynamic_color = COLOR_DUE
        elif queued_list:
            dynamic_color = COLOR_UPCOMING
        elif on_leave_list:
            dynamic_color = COLOR_ON_LEAVE

        embed = discord.Embed(title="🌴 สถานะการลางาน (Leave Board)", color=dynamic_color)
        embed.add_field(name="🟢 กำลังลา (On Leave)", value=_build_truncated_field(on_leave_list, "*(ไม่พบผู้ที่กำลังลา)*"), inline=False)
        embed.add_field(name="⏳ รอคิว (Upcoming)", value=_build_truncated_field(queued_list, "*(ไม่พบคิวล่วงหน้า)*"), inline=False)
        embed.add_field(name="🔴 ครบกำหนด (Due)", value=_build_truncated_field(expired_list, "*(ไม่พบผู้ที่ครบกำหนด)*"), inline=False)
        embed.add_field(name="🕑 ประวัติ (ยังติด Cooldown)", value=_build_truncated_field(history_list, "*(ไม่มีประวัติในช่วงนี้)*"), inline=False)
        embed.set_footer(text=f"อัปเดตเมื่อ: {datetime.now(config.timezone).strftime('%d/%m/%Y %H:%M:%S')}")

        try:
            if message:
                await message.edit(content="", embed=embed)
            else:
                new_msg = await channel.send(embed=embed)
                all_data["leave_board_message_id"] = new_msg.id
                await write_leave_db(all_data)
        except Exception as e:
            print(f"[BreaktimeCog] Error updating leave board: {e}")

    async def ensure_panel_message(self):
        """ตรวจสอบและสร้างปุ่มกดขอลางาน"""
        all_data = await read_leave_db()
        panel_message_id = all_data.get("leave_panel_message_id")
        panel_channel_id = config.get("LEAVE_PANEL_CHANNEL_ID")
        if not panel_channel_id:
            return

        channel = self.bot.get_channel(int(panel_channel_id))
        if not channel:
            return

        if panel_message_id:
            try:
                await channel.fetch_message(int(panel_message_id))
                return
            except (discord.NotFound, discord.HTTPException):
                pass

        try:
            msg = await channel.send("**แผงควบคุมการลางาน (Leave Panel)**", view=LeaveTypeView(self))
            all_data["leave_panel_message_id"] = msg.id
            await write_leave_db(all_data)
        except Exception as e:
            print(f"[BreaktimeCog] Error ensuring leave panel: {e}")

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
                                title="⚠️ แจ้งเตือน: ครบกำหนดการลางานแล้ว",
                                description=(
                                    f"การลาของคุณสิ้นสุดลงเมื่อวันที่ **{u['end_date']}**\n"
                                    f"กรุณากดปุ่ม **'กลับเข้าเวร'** ที่ช่องแผงลางานโดยด่วน\n"
                                    f"หากมีข้อสงสัยโปรดติดต่อ <@{contact_admin_id}>"
                                ),
                                color=discord.Color.red()
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
    @app_commands.command(name="leave", description="เริ่มต้นกระบวนการขอลางาน")
    async def leave_command(self, interaction: discord.Interaction):
        await interaction.response.send_message("กรุณาเลือกประเภทการลาที่ต้องการจากปุ่มด้านล่าง:", view=LeaveTypeView(self), ephemeral=True)

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

    @app_commands.command(name="upload_auth_list", description="[Admin] อัปโหลดไฟล์ .txt รายชื่อผู้มีสิทธิ์ลางาน (MM_YYYY.txt)")
    @app_commands.describe(file="ไฟล์ .txt เช่น 11_2026.txt")
    async def upload_auth_list_command(self, interaction: discord.Interaction, file: discord.Attachment):
        if not config.is_admin(interaction.user.id):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        if not file.filename.endswith(".txt"):
            return await interaction.response.send_message("❌ ต้องเป็นไฟล์นามสกุล `.txt` เท่านั้น", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            os.makedirs(AUTHORIZED_USERS_DIR, exist_ok=True)
            save_path = os.path.join(AUTHORIZED_USERS_DIR, file.filename)
            await file.save(save_path)
            # อ่านจำนวนคน
            user_set = leave_service.read_authorized_users(file.filename.replace(".txt", ""))
            await interaction.followup.send(f"✅ อัปโหลดไฟล์ `{file.filename}` สำเร็จ! (พบผู้มีสิทธิ์ {len(user_set)} คน)", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดในการบันทึกไฟล์: {e}", ephemeral=True)

    @app_commands.command(name="bot_info", description="แสดงเวอร์ชันและสถานะของบอท")
    async def show_bot_info(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="ℹ️ ข้อมูลระบบบอท What Universal",
            color=discord.Color.blue()
        )
        embed.add_field(name="เวอร์ชัน", value=f"`{config.get('BOT_VERSION')}`", inline=True)
        embed.add_field(name="เซิร์ฟเวอร์", value=f"`What Universal ({config.guild_id})`", inline=True)
        embed.add_field(
            name="สถานะระบบ",
            value=(
                f"• Operations: `เปิดทำงาน ✅`\n"
                f"• Breaktime: `เปิดทำงาน ✅`\n"
                f"• Duty Tracker: `{'เปิดใช้งาน' if config.get('DUTY_TRACKER_ENABLED') else 'ปิดใช้งานชั่วคราว ⏸️'}`"
            ),
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    cog = BreaktimeCog(bot)
    await bot.add_cog(cog)
    bot.add_view(LeaveTypeView(cog))
