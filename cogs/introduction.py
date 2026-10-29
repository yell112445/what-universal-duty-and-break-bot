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

WELCOME_BANNER_GIF = "https://media.discordapp.net/attachments/752095905074642945/1341411365839175710/Final.gif?ex=67b5e62f&is=67b494af&hm=79d36fd5a51ec8e8f09ff36628b92d56682a6e359816404b5dd57591530b400f&=&width=578&height=578"
GOOGLE_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLScZseJvMQgMpYzeN-SeqUJYgnznSbbqcuB24xIZP51zQ8MG6w/viewform?usp=sharing&ouid=117385091219907191168"

def build_ticket_embed(ticket: dict, channel_name: str = "") -> discord.Embed:
    role = ticket.get("selected_role")
    fname = ticket.get("first_name")
    lname = ticket.get("last_name")
    phone = ticket.get("phone_number")
    is_medic = (role == "หมอ")
    has_photo = ticket.get("form_image_uploaded", False)

    c_ice = discord.Color.from_rgb(56, 182, 255)
    title_suffix = f" #{channel_name}" if channel_name else ""
    embed = discord.Embed(
        title=f"Registration Ticket{title_suffix}",
        description="-# กรุณากรอกข้อมูลให้ครบเพื่อรับ Role",
        color=c_ice
    )

    role_text = f"`{role}` (Selected)" if role else "`None`"
    embed.add_field(name="1. Department", value=role_text, inline=False)

    if fname and lname:
        info_text = f"`{fname} {lname}` • Phone: `{phone or '-'}`"
    else:
        info_text = "`Pending` (กดปุ่ม Fill Info ด้านล่าง)"
    embed.add_field(name="2. Character Info (IC)", value=info_text, inline=False)

    if is_medic:
        photo_text = "`ไม่ต้องส่งรูป` (ยกเว้นสำหรับหมอ)"
    elif has_photo:
        photo_text = f"`Attached` (แนบรูปแล้ว)\n🔗 [เปิดดูแบบฟอร์ม Google Form]({GOOGLE_FORM_URL})"
    else:
        photo_text = (
            f"🔗 **[คลิกที่นี่เพื่อกรอก Google Form]({GOOGLE_FORM_URL})**\n"
            f"`Pending` (กรอกฟอร์มแล้วแคปภาพหน้าจอส่งในห้องนี้)"
        )
    embed.add_field(name="3. Story / Photo", value=photo_text, inline=False)

    embed.set_footer(text="ระบบจะปลดล็อคปุ่ม Submit อัตโนมัติเมื่อข้อมูลครบ")
    embed.set_thumbnail(url=WELCOME_BANNER_GIF)
    return embed

async def update_ticket_display(channel: discord.TextChannel, ticket_id: str):
    data = await load_welcome_tickets()
    ticket = data.get("tickets", {}).get(ticket_id)
    if not ticket:
        return

    fname = ticket.get("first_name")
    lname = ticket.get("last_name")
    phone = ticket.get("phone_number")
    role = ticket.get("selected_role")
    is_medic = (role == "หมอ")
    has_photo = ticket.get("form_image_uploaded", False)

    is_ready = bool(fname and lname and phone and role and (is_medic or has_photo))

    embed = build_ticket_embed(ticket, channel.name)
    view = WelcomeTicketActiveView(is_ready=is_ready)
    msg_id = ticket.get("display_message_id") or ticket.get("preview_message_id")

    if msg_id:
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.edit(embed=embed, view=view)
            return
        except Exception:
            pass

    try:
        new_msg = await channel.send(embed=embed, view=view)
        ticket["display_message_id"] = new_msg.id
        await save_welcome_tickets(data)
    except Exception as e:
        logger.error(f"[Introduction] Failed to update ticket display: {e}")

# ====================================================
# Modals & Views (Clean Gen-Z Minimalist)
# ====================================================

class CleanCombinedInfoModal(Modal):
    def __init__(self, current_fname: str = "", current_lname: str = "", current_phone: str = ""):
        super().__init__(title="Registration (IC)")
        self.fname_input = TextInput(
            label="First Name (IC)",
            placeholder="เช่น Jelly",
            default=current_fname,
            required=True,
            max_length=30
        )
        self.lname_input = TextInput(
            label="Last Name (IC)",
            placeholder="เช่น Walk",
            default=current_lname,
            required=True,
            max_length=30
        )
        self.phone_input = TextInput(
            label="เบอร์โทรศัพท์ (IC)",
            placeholder="เช่น 123-456",
            default=current_phone,
            required=True,
            max_length=15
        )
        self.add_item(self.fname_input)
        self.add_item(self.lname_input)
        self.add_item(self.phone_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if not ticket:
            return await interaction.followup.send("❌ ไม่พบข้อมูล Ticket ของช่องนี้", ephemeral=True)

        fname = self.fname_input.value.strip()
        lname = self.lname_input.value.strip()
        phone = self.phone_input.value.strip()

        ticket["first_name"] = fname
        ticket["last_name"] = lname
        ticket["full_name"] = f"{fname} {lname}".strip()
        ticket["phone_number"] = phone
        await save_welcome_tickets(data)

        await interaction.followup.send(
            f"✅ บันทึกข้อมูลสำเร็จ:\n• ชื่อ: **{fname} {lname}**\n• เบอร์โทร IC: `{phone}`",
            ephemeral=True
        )
        await update_ticket_display(interaction.channel, ticket_id)

class RoleSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="ตำรวจ (Police)", value="ตำรวจ", description="รับบทบาทหน่วยงานตำรวจ (WUPD)"),
            discord.SelectOption(label="หมอ (Medic)", value="หมอ", description="รับบทบาทหน่วยงานแพทย์ (UMD)")
        ]
        super().__init__(placeholder="เลือกหน่วยงานของคุณ (Police / Medic)...", min_values=1, max_values=1, options=options[:25], custom_id="role_select", row=0)

    async def callback(self, interaction: discord.Interaction):
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if ticket:
            chosen = self.values[0]
            ticket["selected_role"] = chosen
            await save_welcome_tickets(data)
            await interaction.response.send_message(f"✅ คุณเลือกหน่วยงาน: **{chosen}**", ephemeral=True)
            await update_ticket_display(interaction.channel, ticket_id)
        else:
            await interaction.response.send_message("❌ ไม่พบข้อมูล Ticket ของช่องนี้", ephemeral=True)

class CloseTicketConfirmView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger, custom_id="confirm_close_ticket")
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id)

        if ticket and str(interaction.user.id) != ticket.get("user_id"):
            return await interaction.response.send_message("❌ คุณไม่ใช่เจ้าของ Ticket นี้", ephemeral=True)

        await interaction.response.edit_message(content="-# Ticket นี้กำลังจะถูกปิดและลบห้องในอีก 5 วินาที...", embed=None, view=None)
        if ticket_id in data.get("tickets", {}):
            del data["tickets"][ticket_id]
            await save_welcome_tickets(data)
        asyncio.create_task(delete_channel_after_delay(interaction.channel, delay=5))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="cancel_close_ticket")
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        ticket_id = str(interaction.channel.id)
        await interaction.response.edit_message(content="-# ยกเลิกการปิด Ticket แล้ว ดำเนินการต่อได้ตามปกติ", view=None, embed=None)
        await update_ticket_display(interaction.channel, ticket_id)

class WelcomeTicketActiveView(View):
    def __init__(self, is_ready: bool = False):
        super().__init__(timeout=None)
        self.is_ready = is_ready
        self.add_item(RoleSelect())

        # Button 1: Fill Info
        self.btn_info = Button(label="Fill Info", style=discord.ButtonStyle.secondary, custom_id="enter_combined_info", row=1)
        self.btn_info.callback = self.info_callback
        self.add_item(self.btn_info)

        # Button: Google Form Link
        self.btn_form = Button(label="Google Form", style=discord.ButtonStyle.link, url=GOOGLE_FORM_URL, row=1)
        self.add_item(self.btn_form)

        # Button 2: Submit / Pending Info
        submit_label = "Submit" if is_ready else "Pending Info"
        self.btn_submit = Button(label=submit_label, style=discord.ButtonStyle.primary, disabled=not is_ready, custom_id="confirm_welcome", row=1)
        self.btn_submit.callback = self.confirm_callback
        self.add_item(self.btn_submit)

        # Button 3: Close Ticket
        self.btn_close = Button(label="Close Ticket", style=discord.ButtonStyle.secondary, custom_id="close_ticket", row=1)
        self.btn_close.callback = self.close_callback
        self.add_item(self.btn_close)

    async def info_callback(self, interaction: discord.Interaction):
        ticket_id = str(interaction.channel.id)
        data = await load_welcome_tickets()
        ticket = data.get("tickets", {}).get(ticket_id, {})
        fname = ticket.get("first_name", "")
        lname = ticket.get("last_name", "")
        phone = ticket.get("phone_number", "")
        await interaction.response.send_modal(CleanCombinedInfoModal(fname, lname, phone))

    async def confirm_callback(self, interaction: discord.Interaction):
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
        fname = ticket.get("first_name") or ""
        lname = ticket.get("last_name") or ""
        phone = ticket.get("phone_number") or ""
        role = ticket.get("selected_role")

        if not fname or not lname:
            return await interaction.followup.send("⚠️ กรุณากดปุ่ม **กรอกข้อมูล** ให้ครบทั้งชื่อและนามสกุลก่อนยื่นเรื่อง", ephemeral=True)
        if not phone:
            return await interaction.followup.send("⚠️ กรุณากรอกเบอร์โทรศัพท์ IC ก่อนยื่นเรื่อง", ephemeral=True)
        if not role:
            return await interaction.followup.send("⚠️ กรุณาเลือกหน่วยงาน (ตำรวจ หรือ หมอ) ด้านบนก่อนยื่นเรื่อง", ephemeral=True)
        if not is_medic and not ticket.get("form_image_uploaded"):
            return await interaction.followup.send("⚠️ กรุณาแนบภาพแบบฟอร์ม (ส่งรูปภาพลงในห้องนี้) ก่อนยื่นเรื่อง", ephemeral=True)

        ticket["confirmed"] = True
        owner_id = int(ticket["user_id"])
        guild = interaction.guild
        owner = None
        if isinstance(interaction.user, discord.Member) and interaction.user.id == owner_id:
            owner = interaction.user
        elif guild:
            owner = guild.get_member(owner_id)
            if not owner:
                try:
                    owner = await guild.fetch_member(owner_id)
                except Exception as e:
                    logger.warning(f"[Introduction] Could not fetch member {owner_id}: {e}")

        if not owner:
            ticket["confirmed"] = False
            await save_welcome_tickets(data)
            return await interaction.followup.send("❌ ไม่พบสมาชิกเจ้าของ Ticket ในเซิร์ฟเวอร์", ephemeral=True)

        # 1. กำหนด Prefix และยศ
        if ticket["selected_role"] == "ตำรวจ":
            prefix = "[WUPD] "
            # ตำรวจต้องได้รับยศ [1344451915693428909 และ 1334154869556252694]
            p_role = int(config.get("POLICE_ROLE_ID", 1334154869556252694))
            b_role = int(config.get("BEGINNER_ROLE_ID", 1344451915693428909))
            role_ids = [b_role, p_role]
        else:
            prefix = "[UMD] "
            # หมอต้องได้รับยศ 1334154979866316852
            m_role = int(config.get("MD_ROLE_ID", 1334154979866316852))
            role_ids = [m_role]

        phone_str = f" {phone}" if phone else ""
        new_nick = f"{prefix}{fname} {lname}{phone_str}"

        # 2. เปลี่ยนชื่อ Nickname
        nick_changed = False
        try:
            await owner.edit(nick=new_nick[:32])
            nick_changed = True
        except Exception as e:
            logger.warning(f"[Introduction] Could not change nickname for {owner}: {e}")

        # 3. มอบยศ Roles อย่างแม่นยำ (Fetch หากไม่อยู่ใน Cache และรายงานผลชัดเจน)
        roles_assigned = []
        failed_roles = []
        for rid in role_ids:
            role = guild.get_role(rid)
            if not role:
                try:
                    role = await guild.fetch_role(rid)
                except Exception:
                    pass

            if role:
                try:
                    await owner.add_roles(role, reason="Welcome Ticket Completion")
                    roles_assigned.append(role.name)
                except Exception as e:
                    logger.error(f"[Introduction] Failed to add role {role.name} ({role.id}) to {owner}: {e}")
                    failed_roles.append(f"{role.name} (ติดปัญหา Hierarchy/สิทธิ์บอท: {e})")
            else:
                failed_roles.append(f"Role ID {rid} (ไม่พบยศในเซิร์ฟเวอร์)")
                logger.error(f"[Introduction] Role ID {rid} not found in guild {guild.id}")

        # 4. ส่งรูปภาพและ Log เข้าห้อง Welcome Data Log (Dossier)
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
            now_ts = int(datetime.now().timestamp())
            log_embed = discord.Embed(
                title=f"REGISTRATION DOSSIER — {fname.upper()} {lname.upper()}",
                description=(
                    f"**Member:** {owner.display_name} ({owner.mention})\n"
                    f"• First Name: `{fname}`\n"
                    f"• Last Name: `{lname}`\n"
                    f"• In-game Phone: `{phone}`\n"
                    f"• Department: `{ticket['selected_role']}`\n"
                    f"• Assigned Role: `{', '.join(roles_assigned) if roles_assigned else 'ไม่พบ/ติดสิทธิ์'}`\n"
                    f"• Completed At: <t:{now_ts}:F>\n\n"
                    f"-# ข้อมูลได้รับการตรวจสอบและจัดเก็บเข้าฐานข้อมูลเรียบร้อยแล้ว"
                ),
                color=discord.Color.from_rgb(56, 182, 255),
                timestamp=datetime.now(config.timezone)
            )
            log_embed.set_footer(text="What Universal Onboarding Registry")
            if file_for_log:
                log_embed.set_image(url="attachment://form.png")
            try:
                await log_ch.send(embed=log_embed, file=file_for_log)
            except Exception as e:
                logger.error(f"[Introduction] Failed to send welcome data log: {e}")

        del data["tickets"][ticket_id]
        await save_welcome_tickets(data)

        reply_parts = [f"🎉 ยืนยันข้อมูลสำเร็จ! เปลี่ยนชื่อเป็น `{new_nick}` เรียบร้อยแล้ว"]
        if roles_assigned:
            reply_parts.append(f"✅ **มอบยศสำเร็จ:** {', '.join(roles_assigned)}")
        if failed_roles:
            reply_parts.append(f"⚠️ **ไม่สามารถมอบยศบางส่วนได้:**\n• " + "\n• ".join(failed_roles))
            reply_parts.append("-# 👉 คำแนะนำ: หากติดปัญหา Hierarchy กรุณาให้ Admin ลากยศของบอทให้อยู่ **เหนือ** ยศดังกล่าวใน Server Settings > Roles")
        reply_parts.append("-# Ticket นี้จะถูกปิดและลบห้องอัตโนมัติใน 10 วินาที")

        res_msg = "\n".join(reply_parts)
        await interaction.followup.send(res_msg, ephemeral=True)
        asyncio.create_task(delete_channel_after_delay(interaction.channel, delay=10))

    async def close_callback(self, interaction: discord.Interaction):
        confirm_embed = discord.Embed(
            title="Confirm Close Ticket",
            description="คุณต้องการปิด Ticket นี้ใช่หรือไม่?\nห้องนี้และข้อมูลจะถูกลบออกถาวร",
            color=discord.Color.from_rgb(237, 66, 69)
        )
        await interaction.response.send_message(embed=confirm_embed, view=CloseTicketConfirmView(), ephemeral=True)

class WelcomeTicketPanelControlView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(
            label="Google Form (แบบฟอร์ม)",
            style=discord.ButtonStyle.link,
            url=GOOGLE_FORM_URL,
            row=0
        ))

    def is_ticket_admin(self, user: discord.Member) -> bool:
        if config.is_owner(user.id):
            return True
        admin_roles = config.get("TICKET_ADMIN_ROLES", [])
        return any(role.id in admin_roles for role in user.roles)

    @discord.ui.button(label="Open Ticket", style=discord.ButtonStyle.primary, custom_id="start_welcome_ticket")
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

        ticket_record = {
            "user_id": str(user.id),
            "created_at": datetime.now(config.timezone).isoformat(),
            "form_image_uploaded": False,
            "form_image_url": None,
            "first_name": None,
            "last_name": None,
            "full_name": None,
            "phone_number": None,
            "selected_role": None,
            "confirmed": False
        }
        data["tickets"][str(ticket_ch.id)] = ticket_record

        # ส่ง Interactive Card ลงในห้อง Ticket
        active_view = WelcomeTicketActiveView(is_ready=False)
        init_embed = build_ticket_embed(ticket_record, ticket_ch.name)
        display_msg = await ticket_ch.send(
            embed=init_embed,
            view=active_view
        )
        ticket_record["display_message_id"] = display_msg.id
        await save_welcome_tickets(data)

        await interaction.followup.send(f"✅ เปิดห้อง Ticket สำเร็จแล้วที่: {ticket_ch.mention}", ephemeral=True)

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
                await message.channel.send("-# แนบภาพแบบฟอร์มเรียบร้อยแล้ว", delete_after=5)
                await update_ticket_display(message.channel, ticket_id)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        ch_id = config.get("WELCOME_JOIN_LOG_CHANNEL_ID")
        ch = await get_channel_safe(self.bot, ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            c_blurple = discord.Color.from_rgb(88, 101, 242)
            embed = discord.Embed(
                title="MEMBER JOINED",
                description=(
                    f"ยินดีต้อนรับ {member.display_name} ({member.mention}) เข้าสู่ **What Universal**\n"
                    f"• Username: `{member.name}`\n"
                    f"• Account Age: <t:{int(member.created_at.timestamp())}:R>\n\n"
                    f"-# กรุณาไปยังห้อง <#{config.get('WELCOME_TICKET_CHANNEL_ID')}> เพื่อเปิด Ticket ลงทะเบียน"
                ),
                color=c_blurple,
                timestamp=datetime.now(config.timezone)
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await ch.send(embed=embed)
            except Exception as e:
                logger.error(f"[Introduction] Failed to send join log: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        ch_id = config.get("WELCOME_JOIN_LOG_CHANNEL_ID")
        ch = await get_channel_safe(self.bot, ch_id)
        if ch and isinstance(ch, discord.TextChannel):
            c_slate = discord.Color.from_rgb(47, 49, 54)
            joined_str = f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "ไม่ระบุ"
            embed = discord.Embed(
                title="MEMBER LEFT",
                description=(
                    f"{member.display_name} ({member.mention}) ได้ออกจากเซิร์ฟเวอร์\n"
                    f"• Username: `{member.name}`\n"
                    f"• Joined: {joined_str}\n\n"
                    f"-# ระบบบันทึกข้อมูลการออกจากเซิร์ฟเวอร์เรียบร้อยแล้ว"
                ),
                color=c_slate,
                timestamp=datetime.now(config.timezone)
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await ch.send(embed=embed)
            except Exception as e:
                logger.error(f"[Introduction] Failed to send leave log: {e}")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type == discord.InteractionType.component:
            cid = interaction.data.get("custom_id")
            if cid in ["enter_full_name", "enter_phone"]:
                ticket_id = str(interaction.channel.id)
                data = await load_welcome_tickets()
                ticket = data.get("tickets", {}).get(ticket_id, {})
                await interaction.response.send_modal(CleanCombinedInfoModal(ticket.get("first_name", ""), ticket.get("last_name", ""), ticket.get("phone_number", "")))
            elif cid == "enable_system":
                if not config.is_owner(interaction.user.id):
                    return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์จัดการระบบ Ticket", ephemeral=True)
                data = await load_welcome_tickets()
                data.setdefault("_system", {})["enabled"] = True
                await save_welcome_tickets(data)
                await interaction.response.send_message("✅ เปิดใช้งานระบบ Welcome Ticket เรียบร้อยแล้ว", ephemeral=True)
            elif cid == "disable_system":
                if not config.is_owner(interaction.user.id):
                    return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์จัดการระบบ Ticket", ephemeral=True)
                data = await load_welcome_tickets()
                data.setdefault("_system", {})["enabled"] = False
                await save_welcome_tickets(data)
                await interaction.response.send_message("🛑 ปิดใช้งานระบบ Welcome Ticket ชั่วคราวเรียบร้อยแล้ว", ephemeral=True)

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

        embed = discord.Embed(
            title="WHAT UNIVERSAL — REGISTRATION",
            description=(
                "-# ระบบลงทะเบียนและยืนยันตัวตนสมาชิกใหม่\n\n"
                "ยินดีต้อนรับสู่ What Universal\n\n"
                f"📝 **แบบฟอร์มลงทะเบียน:** [คลิกที่นี่เพื่อกรอก Google Form]({GOOGLE_FORM_URL})\n\n"
                "กดปุ่ม **Open Ticket** ด้านล่างเพื่อเริ่มลงทะเบียน"
            ),
            color=discord.Color.from_rgb(47, 49, 54)
        )
        embed.set_footer(text="Automated Registration • 24/7")
        embed.set_image(url=WELCOME_BANNER_GIF)

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
            invite_dm = discord.Embed(
                title="INVITATION LINK",
                description=(
                    f"ลิงก์เชิญเข้าร่วมเซิร์ฟเวอร์ **What Universal** สำหรับคุณ:\n\n"
                    + "\n".join(f"🔗 `{lk}`" for lk in links) + "\n\n"
                    f"• จำนวน: `{count} ลิงก์`\n"
                    f"• อายุลิงก์: `30 นาที` (ใช้ได้ครั้งละ 1 คน)\n\n"
                    f"-# ข้อความนี้จะถูกลบอัตโนมัติภายใน 1 ชั่วโมงเพื่อความปลอดภัย"
                ),
                color=discord.Color.from_rgb(88, 101, 242)
            )
            invite_dm.set_footer(text="What Universal Security Link Service")
            head_msg = await interaction.user.send(embed=invite_dm)
            asyncio.create_task(delete_message_after_delay(head_msg, delay=3600))
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
