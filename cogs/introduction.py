import io
import os
import re
import json
import asyncio
import logging
import aiohttp
from datetime import datetime
from typing import Optional, List, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput, Select

from config_manager import config
from services.storage_service import load_welcome_tickets, save_welcome_tickets

logger = logging.getLogger("Introduction")

async def get_channel_safe(bot: commands.Bot, channel_id: Any) -> Optional[discord.abc.GuildChannel]:
    if not channel_id:
        return None
    try:
        cid = int(channel_id)
        ch = bot.get_channel(cid)
        if ch is not None:
            return ch
        return await bot.fetch_channel(cid)
    except Exception as e:
        logger.warning(f"[Introduction] Cannot fetch channel ID {channel_id}: {e}")
        return None

async def delete_channel_after_delay(channel: discord.TextChannel, delay: int = 10):
    await asyncio.sleep(delay)
    try:
        await channel.delete(reason="Ticket processed or closed")
    except Exception as e:
        logger.warning(f"[Introduction] Failed to delete ticket channel {channel.name}: {e}")

async def delete_message_after_delay(message: discord.Message, delay: int = 3600):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass

def build_preview_embed(ticket: dict) -> discord.Embed:
    embed = discord.Embed(
        title="📋 สถานะข้อมูลของคุณ (Preview)",
        color=discord.Color.blue()
    )
    is_medic = ticket.get("selected_role") == "หมอ"

    # 1. รูปภาพแบบฟอร์ม
    if is_medic:
        img_status = "⚪️ (ไม่ต้องอัปโหลดสำหรับหมอ)"
    elif ticket.get("form_image_uploaded"):
        img_status = "✅ อัปโหลดแล้ว"
    else:
        img_status = "❌ ยังไม่อัปโหลด (ส่งภาพในช่องนี้)"

    # 2. ชื่อ-นามสกุล
    name_val = ticket.get("full_name")
    name_status = f"✅ {name_val}" if name_val else "❌ ยังไม่กรอก"

    # 3. เบอร์โทร IC
    phone_val = ticket.get("phone_number")
    phone_status = f"✅ {phone_val}" if phone_val else "❌ ยังไม่กรอก"

    # 4. บทบาท
    role_val = ticket.get("selected_role")
    role_status = f"✅ {role_val}" if role_val else "❌ ยังไม่เลือก"

    lines = [
        f"• **รูปภาพแบบฟอร์ม:** {img_status}",
        f"• **ชื่อ-นามสกุล:** {name_status}",
        f"• **เบอร์โทร IC:** {phone_status}",
        f"• **บทบาทที่เลือก:** {role_status}"
    ]
    embed.description = "\n".join(lines)

    if ticket.get("form_image_url") and not is_medic:
        embed.set_image(url=ticket["form_image_url"])

    embed.set_footer(text="ข้อมูลจะอัปเดตอัตโนมัติเมื่อทำรายการสำเร็จในแต่ละขั้นตอน")
    return embed

async def update_preview_message(channel: discord.TextChannel, ticket_id: str):
    data = await load_welcome_tickets()
    ticket = data.get("tickets", {}).get(ticket_id)
    if not ticket:
        return

    preview_embed = build_preview_embed(ticket)
    preview_msg_id = ticket.get("preview_message_id")

    if preview_msg_id:
        try:
            msg = await channel.fetch_message(int(preview_msg_id))
            await msg.edit(embed=preview_embed)
            return
        except Exception:
            pass

    try:
        new_msg = await channel.send(embed=preview_embed)
        ticket["preview_message_id"] = new_msg.id
        await save_welcome_tickets(data)
    except Exception as e:
        logger.error(f"[Introduction] Failed to send preview embed: {e}")

# ====================================================
# Modals & Views
# ====================================================
class FullNameModal(Modal):
    def __init__(self):
        super().__init__(title="กรอกชื่อและนามสกุล (IC)")
        self.full_name_input = TextInput(
            label="ชื่อและนามสกุล",
            placeholder="ตัวอย่าง: John Doe (มีเว้นวรรคระหว่างชื่อ-สกุล)",
            max_length=40,
            required=True
        )
        self.add_item(self.full_name_input)

    async def on_submit(self, interaction: discord.Interaction):
        name = self.full_name_input.value.strip()
        if " " not in name:
            return await interaction.response.send_message("❌ กรุณาเว้นวรรคระหว่างชื่อและนามสกุล เช่น `John Doe`", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if ticket:
            ticket["full_name"] = name
            await save_welcome_tickets(data)
            await interaction.followup.send(f"✅ บันทึกชื่อของคุณแล้ว: **{name}**", ephemeral=True)
            await update_preview_message(interaction.channel, ticket_id)
        else:
            await interaction.followup.send("❌ ไม่พบข้อมูล Ticket ของช่องนี้", ephemeral=True)

class PhoneModal(Modal):
    def __init__(self):
        super().__init__(title="กรอกเบอร์โทรศัพท์ (IC)")
        self.phone_input = TextInput(
            label="เบอร์โทรศัพท์ในเกม",
            placeholder="ตัวอย่าง: 123456",
            max_length=15,
            required=True
        )
        self.add_item(self.phone_input)

    async def on_submit(self, interaction: discord.Interaction):
        number = self.phone_input.value.strip()
        await interaction.response.defer(ephemeral=True)

        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if ticket:
            ticket["phone_number"] = number
            await save_welcome_tickets(data)
            await interaction.followup.send(f"✅ บันทึกเบอร์โทรแล้ว: **{number}**", ephemeral=True)
            await update_preview_message(interaction.channel, ticket_id)
        else:
            await interaction.followup.send("❌ ไม่พบข้อมูล Ticket ของช่องนี้", ephemeral=True)

class RoleSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="ตำรวจ", value="ตำรวจ", description="รับบทบาทหน่วยงานตำรวจ (WUPD)", emoji="👮"),
            discord.SelectOption(label="หมอ", value="หมอ", description="รับบทบาทหน่วยงานแพทย์ (UMD)", emoji="👨‍⚕️")
        ]
        super().__init__(placeholder="เลือกบทบาทของคุณ...", min_values=1, max_values=1, options=options, custom_id="role_select")

    async def callback(self, interaction: discord.Interaction):
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if ticket:
            chosen = self.values[0]
            ticket["selected_role"] = chosen
            await save_welcome_tickets(data)
            await interaction.response.send_message(f"✅ คุณเลือกบทบาท: **{chosen}**", ephemeral=True)
            await update_preview_message(interaction.channel, ticket_id)
        else:
            await interaction.response.send_message("❌ ไม่พบข้อมูล Ticket ของช่องนี้", ephemeral=True)

class CloseTicketConfirmView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ ยืนยันปิด Ticket", style=discord.ButtonStyle.danger, custom_id="confirm_close_ticket")
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if ticket and str(interaction.user.id) != ticket.get("user_id"):
            return await interaction.response.send_message("❌ คุณไม่ใช่เจ้าของ Ticket นี้", ephemeral=True)

        await interaction.response.edit_message(content="🛑 ดำเนินการปิดและลบ Ticket ใน 5 วินาที...", embed=None, view=None)
        if ticket_id in data.get("tickets", {}):
            del data["tickets"][ticket_id]
            await save_welcome_tickets(data)
        asyncio.create_task(delete_channel_after_delay(interaction.channel, delay=5))

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.secondary, custom_id="cancel_close_ticket")
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        ticket_id = str(interaction.channel.id)
        await interaction.response.edit_message(content="การปิด Ticket ถูกยกเลิกแล้ว", view=None, embed=None)
        await update_preview_message(interaction.channel, ticket_id)

class WelcomeTicketActiveView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(RoleSelect())

    @discord.ui.button(label="✏️ กรอกชื่อ-นามสกุล", style=discord.ButtonStyle.primary, custom_id="enter_full_name", row=1)
    async def name_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(FullNameModal())

    @discord.ui.button(label="📞 กรอกเบอร์โทร IC", style=discord.ButtonStyle.secondary, custom_id="enter_phone", row=1)
    async def phone_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(PhoneModal())

    @discord.ui.button(label="✅ ยืนยันข้อมูลทั้งหมด", style=discord.ButtonStyle.success, custom_id="confirm_welcome", row=2)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if not ticket:
            return await interaction.followup.send("❌ ไม่พบข้อมูล Ticket", ephemeral=True)
        if ticket.get("confirmed"):
            return await interaction.followup.send("⚠️ ข้อมูลนี้ได้รับการยืนยันไปแล้ว", ephemeral=True)

        is_medic = ticket.get("selected_role") == "หมอ"
        # ตรวจสอบความครบถ้วน
        if not ticket.get("full_name"):
            return await interaction.followup.send("⚠️ กรุณากดปุ่มกรอกชื่อ-นามสกุลให้เรียบร้อยก่อนยืนยัน", ephemeral=True)
        if not ticket.get("selected_role"):
            return await interaction.followup.send("⚠️ กรุณาเลือกบทบาท (ตำรวจ หรือ หมอ) ก่อนยืนยัน", ephemeral=True)
        if not is_medic and not ticket.get("form_image_uploaded"):
            return await interaction.followup.send("⚠️ กรุณาอัปโหลดภาพแบบฟอร์ม (ส่งภาพในช่องนี้) ก่อนยืนยัน", ephemeral=True)

        ticket["confirmed"] = True
        owner_id = int(ticket["user_id"])
        guild = interaction.guild
        owner = guild.get_member(owner_id) if guild else None

        if not owner:
            ticket["confirmed"] = False
            await save_welcome_tickets(data)
            return await interaction.followup.send("❌ ไม่พบสมาชิกเจ้าของ Ticket ในเซิร์ฟเวอร์", ephemeral=True)

        # 1. กำหนด Prefix และยศ
        if ticket["selected_role"] == "ตำรวจ":
            prefix = "[WUPD] "
            p_role = int(config.get("POLICE_ROLE_ID", 1334154869556252694))
            b_role = int(config.get("BEGINNER_ROLE_ID", 1344451915693428909))
            role_ids = [p_role, b_role]
        else:
            prefix = "[UMD] "
            m_role = int(config.get("MD_ROLE_ID", 1334154979866316852))
            role_ids = [m_role]

        phone_str = f" {ticket.get('phone_number')}" if ticket.get("phone_number") else ""
        new_nick = f"{prefix}{ticket['full_name']}{phone_str}"

        # 2. เปลี่ยนชื่อ Nickname
        nick_changed = False
        if guild.me.guild_permissions.manage_nicknames:
            try:
                await owner.edit(nick=new_nick[:32])
                nick_changed = True
            except Exception as e:
                logger.warning(f"[Introduction] Could not change nickname for {owner}: {e}")

        # 3. มอบยศ Roles
        roles_assigned = []
        if guild.me.guild_permissions.manage_roles:
            to_add = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid)]
            if to_add:
                try:
                    await owner.add_roles(*to_add, reason="Welcome Ticket Completion")
                    roles_assigned = [r.name for r in to_add]
                except Exception as e:
                    logger.warning(f"[Introduction] Could not add roles to {owner}: {e}")

        # 4. ส่งรูปภาพและ Log เข้าห้อง Welcome Data Log
        log_ch_id = config.get("WELCOME_DATA_LOG_CHANNEL_ID")
        log_ch = await get_channel_safe(interaction.client, log_ch_id)
        file_for_log = None

        if ticket.get("form_image_url") and not is_medic:
            try:
                session = getattr(interaction.client, "http_session", None)
                if not session:
                    session = aiohttp.ClientSession()
                    close_session = True
                else:
                    close_session = False

                async with session.get(ticket["form_image_url"], timeout=10) as resp:
                    if resp.status == 200:
                        img_bytes = await resp.read()
                        file_for_log = discord.File(io.BytesIO(img_bytes), filename="form.png")

                if close_session:
                    await session.close()
            except Exception as e:
                logger.warning(f"[Introduction] Failed to download form image: {e}")

        if log_ch:
            log_embed = discord.Embed(
                title="✅ ยืนยันข้อมูล Welcome Ticket สำเร็จ",
                description=(
                    f"**ผู้ใช้:** {owner.mention} (`{owner.id}`)\n"
                    f"**ชื่อ-นามสกุล:** {ticket['full_name']}\n"
                    f"**เบอร์โทร IC:** {ticket.get('phone_number', '-')}\n"
                    f"**บทบาท:** {ticket['selected_role']}\n"
                    f"**ชื่อใหม่:** `{new_nick}`\n"
                    f"**ยศที่มอบ:** {', '.join(roles_assigned) if roles_assigned else 'ไม่พบ/ติดสิทธิ์'}"
                ),
                color=discord.Color.green(),
                timestamp=datetime.now(config.timezone)
            )
            if file_for_log:
                log_embed.set_image(url="attachment://form.png")
            try:
                await log_ch.send(embed=log_embed, file=file_for_log)
            except Exception as e:
                logger.error(f"[Introduction] Failed to send welcome data log: {e}")

        del data["tickets"][ticket_id]
        await save_welcome_tickets(data)

        res_msg = f"🎉 ยืนยันข้อมูลสำเร็จ! เปลี่ยนชื่อเป็น `{new_nick}` และมอบยศเรียบร้อยแล้ว\n(ห้อง Ticket นี้จะถูกลบอัตโนมัติใน 10 วินาที)"
        await interaction.followup.send(res_msg, ephemeral=True)
        asyncio.create_task(delete_channel_after_delay(interaction.channel, delay=10))

    @discord.ui.button(label="❌ ปิด/ยกเลิก Ticket", style=discord.ButtonStyle.danger, custom_id="close_ticket", row=2)
    async def close_button(self, interaction: discord.Interaction, button: Button):
        confirm_embed = discord.Embed(
            title="🛑 ปิด Ticket",
            description="คุณแน่ใจหรือไม่ว่าต้องการปิดและยกเลิก Ticket นี้?\n\nกด **✅ ยืนยัน** เพื่อปิดและลบห้อง\nกด **❌ ยกเลิก** เพื่อทำรายการต่อ",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=confirm_embed, view=CloseTicketConfirmView(), ephemeral=True)

class WelcomeTicketPanelControlView(View):
    def __init__(self):
        super().__init__(timeout=None)

    def is_ticket_admin(self, user: discord.Member) -> bool:
        if config.is_owner(user.id):
            return True
        admin_roles = config.get("TICKET_ADMIN_ROLES", [])
        return any(role.id in admin_roles for role in user.roles)

    @discord.ui.button(label="🎫 เปิด Welcome Ticket", style=discord.ButtonStyle.primary, custom_id="start_welcome_ticket")
    async def start_ticket_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        data = await load_welcome_tickets()

        if not data.get("_system", {}).get("enabled", True):
            return await interaction.followup.send("❌ ขณะนี้ระบบ Welcome Ticket ปิดรับบริการชั่วคราว", ephemeral=True)

        user = interaction.user
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ ไม่สามารถทำรายการนอกเซิร์ฟเวอร์ได้", ephemeral=True)

        # เช็กว่ามี ticket เดิมที่เปิดค้างอยู่หรือไม่
        for tid, tinfo in data.get("tickets", {}).items():
            if str(tinfo.get("user_id")) == str(user.id):
                existing_ch = guild.get_channel(int(tid))
                if existing_ch:
                    return await interaction.followup.send(f"⚠️ คุณมีห้อง Ticket เปิดค้างอยู่แล้ว: {existing_ch.mention}", ephemeral=True)

        # สร้างห้อง Ticket ใน Category
        cat_id = config.get("WELCOME_TICKET_CATEGORY_ID")
        category = guild.get_channel(int(cat_id)) if cat_id else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, manage_channels=True)
        }
        for admin_rid in config.get("TICKET_ADMIN_ROLES", []):
            admin_role = guild.get_role(int(admin_rid))
            if admin_role:
                overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        ch_name = f"welcome-{user.name}"
        try:
            ticket_ch = await guild.create_text_channel(
                name=ch_name,
                category=category if isinstance(category, discord.CategoryChannel) else None,
                overwrites=overwrites,
                reason=f"Welcome Ticket created for {user.name}"
            )
        except Exception as e:
            return await interaction.followup.send(f"❌ ไม่สามารถสร้างห้อง Ticket ได้: {e}", ephemeral=True)

        data["tickets"][str(ticket_ch.id)] = {
            "user_id": str(user.id),
            "created_at": datetime.now(config.timezone).isoformat(),
            "form_image_uploaded": False,
            "form_image_url": None,
            "full_name": None,
            "phone_number": None,
            "selected_role": None,
            "confirmed": False
        }
        await save_welcome_tickets(data)

        # ส่งข้อความแนะนำในห้อง Ticket
        intro_embed = discord.Embed(
            title="👋 ยินดีต้อนรับสู่ What Universal",
            description=(
                f"สวัสดี {user.mention} กรุณาดำเนินการตามขั้นตอนดังนี้:\n\n"
                "**1. ส่งภาพแบบฟอร์ม:** แคปหน้าจอแบบฟอร์มแล้วส่งเป็นรูปภาพในช่องนี้ *(หากเลือกหมอไม่ต้องส่ง)*\n"
                "**2. กรอกชื่อ-นามสกุล:** กดปุ่ม `✏️ กรอกชื่อ-นามสกุล` ด้านล่าง\n"
                "**3. กรอกเบอร์โทร IC:** กดปุ่ม `📞 กรอกเบอร์โทร IC`\n"
                "**4. เลือกบทบาท:** เลือกตำรวจ หรือ หมอ จากเมนูด้านล่าง\n"
                "**5. ยืนยันข้อมูล:** เมื่อข้อมูลครบถ้วน ให้กดปุ่ม `✅ ยืนยันข้อมูลทั้งหมด`\n\n"
                "*(สามารถทำขั้นตอนใดก่อนก็ได้)*"
            ),
            color=discord.Color.green()
        )
        active_view = WelcomeTicketActiveView()
        await ticket_ch.send(embed=intro_embed, view=active_view)
        await update_preview_message(ticket_ch, str(ticket_ch.id))

        await interaction.followup.send(f"✅ เปิดห้อง Ticket สำเร็จแล้วที่: {ticket_ch.mention}", ephemeral=True)

    @discord.ui.button(label="🟢 เปิดระบบ", style=discord.ButtonStyle.success, custom_id="enable_system")
    async def enable_system_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_ticket_admin(interaction.user):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์จัดการระบบ Ticket", ephemeral=True)

        data = await load_welcome_tickets()
        data["_system"]["enabled"] = True
        await save_welcome_tickets(data)
        await interaction.response.send_message("✅ เปิดใช้งานระบบ Welcome Ticket เรียบร้อยแล้ว", ephemeral=True)

    @discord.ui.button(label="🔴 ปิดระบบ", style=discord.ButtonStyle.danger, custom_id="disable_system")
    async def disable_system_button(self, interaction: discord.Interaction, button: Button):
        if not self.is_ticket_admin(interaction.user):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์จัดการระบบ Ticket", ephemeral=True)

        data = await load_welcome_tickets()
        data["_system"]["enabled"] = False
        await save_welcome_tickets(data)
        await interaction.response.send_message("🛑 ปิดใช้งานระบบ Welcome Ticket ชั่วคราวเรียบร้อยแล้ว", ephemeral=True)

# ====================================================
# Introduction Cog
# ====================================================
class IntroductionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cleanup_tickets_loop.start()

    def cog_unload(self):
        self.cleanup_tickets_loop.cancel()

    @tasks.loop(minutes=10)
    async def cleanup_tickets_loop(self):
        """ล้างประวัติ Ticket ที่ห้องใน Discord ถูกลบไปแล้ว"""
        try:
            data = await load_welcome_tickets()
            tickets = data.get("tickets", {})
            to_remove = []
            for cid in list(tickets.keys()):
                ch = self.bot.get_channel(int(cid))
                if ch is None:
                    to_remove.append(cid)

            if to_remove:
                for cid in to_remove:
                    tickets.pop(cid, None)
                await save_welcome_tickets(data)
                logger.info(f"[Introduction] Cleaned up {len(to_remove)} removed ticket channels.")
        except Exception as e:
            logger.error(f"[Introduction] cleanup_tickets_loop error: {e}")

    @cleanup_tickets_loop.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    # --- Listeners ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        ticket_id = str(message.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if ticket and str(message.author.id) == str(ticket.get("user_id")):
            if message.attachments:
                att = message.attachments[0]
                if not att.filename.lower().endswith(('png', 'jpg', 'jpeg', 'gif', 'webp')):
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return await message.channel.send("❌ อนุญาตเฉพาะไฟล์รูปภาพเท่านั้น!", delete_after=5)

                ticket["form_image_uploaded"] = True
                ticket["form_image_url"] = att.url
                await save_welcome_tickets(data)

                try:
                    await message.add_reaction("✅")
                except Exception:
                    pass
                await message.channel.send("✅ อัปโหลดภาพแบบฟอร์มสำเร็จ!", delete_after=5)
                await update_preview_message(message.channel, ticket_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        ch_id = config.get("WELCOME_JOIN_LOG_CHANNEL_ID")
        ch = await get_channel_safe(self.bot, ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            embed = discord.Embed(
                title="👋 สมาชิกใหม่เข้าร่วมเซิร์ฟเวอร์",
                description=f"ยินดีต้อนรับ {member.mention} (`{member.name}`) เข้าสู่เซิร์ฟเวอร์!",
                color=discord.Color.green(),
                timestamp=datetime.now(config.timezone)
            )
            if member.avatar:
                embed.set_thumbnail(url=member.avatar.url)
            embed.set_footer(text=f"User ID: {member.id}")
            try:
                await ch.send(embed=embed)
            except Exception as e:
                logger.error(f"[Introduction] Failed to send join log: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        ch_id = config.get("WELCOME_JOIN_LOG_CHANNEL_ID")
        ch = await get_channel_safe(self.bot, ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            embed = discord.Embed(
                title="🚪 สมาชิกออกจากเซิร์ฟเวอร์",
                description=f"{member.mention} (`{member.name}`) ได้ออกจากเซิร์ฟเวอร์แล้ว",
                color=discord.Color.red(),
                timestamp=datetime.now(config.timezone)
            )
            if member.avatar:
                embed.set_thumbnail(url=member.avatar.url)
            embed.set_footer(text=f"User ID: {member.id}")
            try:
                await ch.send(embed=embed)
            except Exception as e:
                logger.error(f"[Introduction] Failed to send leave log: {e}")

    # --- Slash Commands ---
    @app_commands.command(name="welcome_setup", description="[Owner] ส่งแผงควบคุม Welcome Ticket ลงในห้องที่กำหนด")
    async def welcome_setup(self, interaction: discord.Interaction):
        if not config.is_owner(interaction.user.id):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        ch_id = config.get("WELCOME_TICKET_CHANNEL_ID")
        channel = await get_channel_safe(self.bot, ch_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send(f"❌ ไม่พบ Text Channel ของ Welcome Ticket (ID: {ch_id})", ephemeral=True)

        data = await load_welcome_tickets()
        is_enabled = data.get("_system", {}).get("enabled", True)

        embed = discord.Embed(
            title="🎫 ระบบ Welcome Ticket (ลงทะเบียนรับผู้เล่นใหม่)",
            description=(
                "ยินดีต้อนรับสู่หน่วยงาน What Universal!\n\n"
                "กดปุ่ม **'🎫 เปิด Welcome Ticket'** ด้านล่างเพื่อเริ่มกระบวนการลงทะเบียน กรอกชื่อ และรับยศหน่วยงาน\n\n"
                f"**สถานะระบบ:** {'🟢 เปิดให้บริการ' if is_enabled else '🔴 ปิดให้บริการชั่วคราว'}"
            ),
            color=discord.Color.blue()
        )
        thumb = config.get("EMBED_THUMBNAIL_URL")
        if thumb:
            embed.set_image(url=thumb)
        embed.set_footer(text=f"{config.get('BOT_VERSION')} | Welcome System")

        view = WelcomeTicketPanelControlView()
        await channel.send(embed=embed, view=view)
        await interaction.followup.send(f"✅ ส่งแผง Welcome Ticket ไปยังห้อง {channel.mention} เรียบร้อยแล้ว!", ephemeral=True)

    @app_commands.command(name="generate_link", description="สร้างลิงก์เชิญแบบใช้ได้ครั้งเดียว (หมดอายุใน 30 นาที)")
    @app_commands.describe(count="จำนวนลิงก์ที่ต้องการสร้าง (1 - 10 ลิงก์)")
    async def generate_link(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 10] = 1):
        if not interaction.guild:
            return await interaction.response.send_message("คำสั่งนี้ใช้ได้เฉพาะในเซิร์ฟเวอร์", ephemeral=True)

        # ตรวจสอบสิทธิ์ (Owner หรือมียศ Assistant)
        has_perm = config.is_owner(interaction.user.id)
        ast_role_id = config.get("ASSISTANT_ROLE_ID")
        if not has_perm and ast_role_id:
            ast_role = interaction.guild.get_role(int(ast_role_id))
            if ast_role and ast_role in interaction.user.roles:
                has_perm = True

        if not has_perm:
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์ใช้งานคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        links = []
        for i in range(count):
            try:
                invite = await interaction.channel.create_invite(
                    max_uses=1,
                    max_age=1800,
                    unique=True,
                    reason=f"Generated by {interaction.user.name} via /generate_link"
                )
                links.append(invite.url)
            except Exception as e:
                return await interaction.followup.send(f"❌ บอทไม่มีสิทธิ์สร้างลิงก์เชิญในห้องนี้: {e}", ephemeral=True)

        # ส่งเข้า DM
        dm_ok = False
        try:
            head_msg = await interaction.user.send(f"**🔗 ลิงก์เชิญเซิร์ฟเวอร์ (ใช้ได้คนละ 1 ครั้ง / หมดอายุใน 30 นาที):**")
            asyncio.create_task(delete_message_after_delay(head_msg, delay=3600))
            for lk in links:
                l_msg = await interaction.user.send(lk)
                asyncio.create_task(delete_message_after_delay(l_msg, delay=3600))
                await asyncio.sleep(0.2)
            dm_ok = True
        except Exception:
            dm_ok = False

        # ส่ง Log
        log_ch_id = config.get("LINK_LOG_CHANNEL_ID")
        log_ch = await get_channel_safe(self.bot, log_ch_id)
        if log_ch:
            log_embed = discord.Embed(
                title="🔗 บันทึกการสร้าง Invite Link",
                description=(
                    f"**ผู้สร้าง:** {interaction.user.mention} (`{interaction.user.id}`)\n"
                    f"**จำนวน:** {count} ลิงก์\n"
                    f"**ห้องที่สร้าง:** {interaction.channel.mention}"
                ),
                color=discord.Color.blue(),
                timestamp=datetime.now(config.timezone)
            )
            log_embed.add_field(name="ลิงก์ที่สร้าง:", value="\n".join(links), inline=False)
            try:
                await log_ch.send(embed=log_embed)
            except Exception as e:
                logger.error(f"[Introduction] Failed to send link log: {e}")

        reply_text = f"✅ สร้างลิงก์เชิญสำเร็จจำนวน {count} ลิงก์!"
        if dm_ok:
            reply_text += "\n📬 ลิงก์ถูกส่งเข้า DM ของคุณแล้ว (จะถูกลบอัตโนมัติใน 1 ชั่วโมง)"
        else:
            reply_text += "\n⚠️ ไม่สามารถส่งเข้า DM ของคุณได้ (คุณอาจปิดรับ DM จากสมาชิกเซิร์ฟเวอร์) แต่ลิงก์ถูกบันทึกในห้อง Log แล้ว"

        await interaction.followup.send(reply_text, ephemeral=True)

    @app_commands.command(name="clean_dm", description="[Owner] ลบข้อความที่บอทเคยส่งหาคุณในแชทส่วนตัว (DM)")
    async def clean_dm(self, interaction: discord.Interaction):
        if not config.is_owner(interaction.user.id):
            return await interaction.response.send_message("❌ คำสั่งนี้สงวนไว้สำหรับ Owner เท่านั้น", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            dm_channel = interaction.user.dm_channel or await interaction.user.create_dm()
        except Exception as e:
            return await interaction.followup.send(f"❌ ไม่สามารถเปิด DM ได้: {e}", ephemeral=True)

        deleted = 0
        try:
            async for msg in dm_channel.history(limit=100):
                if msg.author == self.bot.user:
                    try:
                        await msg.delete()
                        deleted += 1
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
            await interaction.followup.send(f"✅ ลบข้อความของบอทใน DM สำเร็จแล้ว {deleted} ข้อความ", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดขณะลบข้อความ: {e}", ephemeral=True)

    @app_commands.command(name="mass_update_nickname_prefix", description="[Owner] อัปเดต Prefix หน้าชื่อเล่นให้สมาชิกในยศที่เลือก")
    @app_commands.describe(role="ยศที่ต้องการเปลี่ยน Prefix", old_prefix="Prefix เดิม เช่น [Police]", new_prefix="Prefix ใหม่ เช่น [WUPD]")
    async def mass_update_nickname_prefix(self, interaction: discord.Interaction, role: discord.Role, old_prefix: str, new_prefix: str):
        if not config.is_owner(interaction.user.id):
            return await interaction.response.send_message("❌ คำสั่งนี้สงวนไว้สำหรับ Owner เท่านั้น", ephemeral=True)

        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("คำสั่งนี้ใช้ได้เฉพาะในเซิร์ฟเวอร์", ephemeral=True)

        if not guild.me.guild_permissions.manage_nicknames:
            return await interaction.response.send_message("❌ บอทไม่มีสิทธิ์ Manage Nicknames", ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        members_to_update = [m for m in role.members if not m.bot]
        updated_count = 0
        failed_count = 0

        old_clean = old_prefix.strip()
        new_clean = new_prefix.strip()

        for member in members_to_update:
            current_nick = member.display_name
            # เช็กว่ามี old_prefix อยู่ในชื่อหรือไม่
            if old_clean.lower() in current_nick.lower():
                # แทนที่ old_prefix ด้วย new_prefix
                idx = current_nick.lower().find(old_clean.lower())
                updated_nick = (current_nick[:idx] + new_clean + current_nick[idx + len(old_clean):]).strip()
            else:
                # ถ้าไม่มี ให้เติม new_prefix ข้างหน้า
                updated_nick = f"{new_clean} {current_nick}".strip()

            try:
                await member.edit(nick=updated_nick[:32])
                updated_count += 1
                await asyncio.sleep(0.3)
            except Exception:
                failed_count += 1

        await interaction.followup.send(
            f"✅ ดำเนินการอัปเดต Prefix สำหรับยศ **{role.name}** สำเร็จ!\n"
            f"• เปลี่ยนสำเร็จ: **{updated_count} คน**\n"
            f"• ไม่สำเร็จ/ติดสิทธิ์: **{failed_count} คน**",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    cog = IntroductionCog(bot)
    await bot.add_cog(cog)
    bot.add_view(WelcomeTicketPanelControlView())
    bot.add_view(WelcomeTicketActiveView())
    bot.add_view(CloseTicketConfirmView())
