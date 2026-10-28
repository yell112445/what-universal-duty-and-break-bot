import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Button, Select
from datetime import datetime, timedelta
from typing import Optional, List
from config_manager import config

# ==============================================================================
# Constants & Helpers
# ==============================================================================

WELCOME_BANNER_GIF = "https://media.discordapp.net/attachments/752095905074642945/1341411365839175710/Final.gif?ex=67b5e62f&is=67b494af&hm=79d36fd5a51ec8e8f09ff36628b92d56682a6e359816404b5dd57591530b400f&=&width=578&height=578"

def chunk_list(items: list, chunk_size: int):
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]

def build_roster_embeds(
    title: str,
    description: str,
    color: discord.Color,
    roster_lines: Optional[list[str]] = None,
    section_name: str = "รายชื่อคนที่ Check-in อยู่",
    items_per_field: int = 10,
    footer_text: str = "What Universal • อัปเดตข้อมูลอัตโนมัติ",
    thumbnail_url: Optional[str] = None,
    extra_fields: Optional[list[dict]] = None
) -> list[discord.Embed]:
    if not roster_lines:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )
        if extra_fields:
            for f in extra_fields:
                embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
        if roster_lines is not None and len(roster_lines) == 0 and not extra_fields:
            embed.add_field(name=section_name, value="*— ไม่มีรายชื่อในขณะนี้ —*", inline=False)
        embed.set_footer(text=footer_text)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        return [embed]

    chunks = list(chunk_list(roster_lines, items_per_field))
    embeds = []

    main_embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )
    if extra_fields:
        for f in extra_fields:
            main_embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))

    first_batch = chunks[:2]
    total_count = len(roster_lines)

    for idx, c in enumerate(first_batch):
        start_no = idx * items_per_field + 1
        end_no = start_no + len(c) - 1
        field_label = f"{section_name} [{start_no}–{end_no} จาก {total_count}]" if len(chunks) > 1 else f"{section_name} [{total_count} คน]"
        main_embed.add_field(
            name=field_label,
            value="\n".join(c),
            inline=False
        )

    main_embed.set_footer(text=f"{footer_text} • หน้า 1/{max(1, len(chunks) // 2 + (1 if len(chunks) % 2 else 0))}")
    if thumbnail_url:
        main_embed.set_thumbnail(url=thumbnail_url)
    embeds.append(main_embed)

    remaining_chunks = chunks[2:]
    page_no = 2
    for sub_chunks in chunk_list(remaining_chunks, 2):
        sub_embed = discord.Embed(
            title=f"{title} (หน้า {page_no})",
            description=f"-# รายชื่อต่อเนื่อง",
            color=color
        )
        for idx_sub, c in enumerate(sub_chunks):
            current_chunk_idx = 2 + (page_no - 2) * 2 + idx_sub
            start_no = current_chunk_idx * items_per_field + 1
            end_no = start_no + len(c) - 1
            sub_embed.add_field(
                name=f"{section_name} [{start_no}–{end_no} จาก {total_count}]",
                value="\n".join(c),
                inline=False
            )
        sub_embed.set_footer(text=f"{footer_text} • หน้า {page_no}")
        embeds.append(sub_embed)
        page_no += 1

    return embeds


# ==============================================================================
# 1. Operations Components (Time Tracker)
# ==============================================================================

class CleanOTPModal(Modal):
    def __init__(self):
        super().__init__(title="OTP Verification")
        self.otp_input = TextInput(
            label="รหัส OTP 4 หลัก",
            placeholder="กรอกรหัส 4 หลัก เช่น 1234",
            required=True,
            min_length=4,
            max_length=4
        )
        self.add_item(self.otp_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"**Check-in สำเร็จ**\n"
            f"-# ยืนยันรหัส OTP `{self.otp_input.value.strip()}` เรียบร้อยแล้ว\n"
            f"ระบบเริ่มบันทึกเวลาทำงานของคุณตั้งแต่เวลานี้",
            ephemeral=True
        )

class CleanOperationView(View):
    def __init__(self):
        super().__init__(timeout=None)

    # ปุ่ม 1: ฟ้า (Primary)
    @discord.ui.button(label="Check-in", style=discord.ButtonStyle.primary, custom_id="clean_op_checkin")
    async def checkin_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(CleanOTPModal())

    # ปุ่ม 2: เทา (Secondary)
    @discord.ui.button(label="Check-out", style=discord.ButtonStyle.secondary, custom_id="clean_op_checkout")
    async def checkout_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="CHECK-OUT SUCCESS",
            description=(
                f"**ผู้ใช้งาน:** {interaction.user.display_name} ({interaction.user.mention})\n"
                f"• Check-in: `10:00:00`\n"
                f"• Check-out: `{datetime.now(config.timezone).strftime('%H:%M:%S')}`\n"
                f"• รวมเวลาทำงาน: `2 ชม. 15 นาที`\n\n"
                f"-# ระบบบันทึกข้อมูลเรียบร้อยแล้ว ขอบคุณสำหรับการทำงานครับ"
            ),
            color=discord.Color.from_rgb(88, 101, 242)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ปุ่ม 3: เทา (Secondary)
    @discord.ui.button(label="My Status", style=discord.ButtonStyle.secondary, custom_id="clean_op_status")
    async def status_button(self, interaction: discord.Interaction, button: Button):
        now_ts = int(datetime.now().timestamp())
        embed = discord.Embed(
            title="STATUS REPORT",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.add_field(name="สถานะปัจจุบัน", value="`Online` (กำลังทำงาน)", inline=True)
        embed.add_field(name="เริ่มงานเมื่อ", value=f"<t:{now_ts - 3600}:R>", inline=True)
        embed.add_field(name="เวลารวมวันนี้", value="`3 ชั่วโมง 40 นาที`", inline=False)
        embed.set_footer(text="What Universal • ซิงค์ข้อมูลเรียลไทม์")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# 2. Welcome & Ticket Components (Clean Gen-Z)
# ==============================================================================

class CleanCloseConfirmView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger, custom_id="preview_confirm_close_ticket")
    async def confirm_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "-# Ticket นี้กำลังจะถูกปิดและลบห้องในอีก 5 วินาที...",
            ephemeral=True
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, custom_id="preview_cancel_close_ticket")
    async def cancel_btn(self, interaction: discord.Interaction, button: Button):
        await interaction.response.edit_message(content="-# ยกเลิกการปิด Ticket แล้ว ดำเนินการต่อได้ตามปกติ", embed=None, view=None)

class CleanCombinedInfoModal(Modal):
    def __init__(self, parent_view: 'CleanTicketInteractiveView'):
        super().__init__(title="Registration (IC)")
        self.parent_view = parent_view
        
        self.fname_input = TextInput(
            label="First Name (IC)",
            placeholder="เช่น Jelly",
            default=self.parent_view.first_name or "",
            required=True,
            max_length=30
        )
        self.lname_input = TextInput(
            label="Last Name (IC)",
            placeholder="เช่น Walk",
            default=self.parent_view.last_name or "",
            required=True,
            max_length=30
        )
        self.phone_input = TextInput(
            label="Phone Number (IC)",
            placeholder="เช่น 123-456 หรือ 555234",
            default=self.parent_view.phone or "",
            required=True,
            max_length=15
        )
        self.add_item(self.fname_input)
        self.add_item(self.lname_input)
        self.add_item(self.phone_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.first_name = self.fname_input.value.strip()
        self.parent_view.last_name = self.lname_input.value.strip()
        self.parent_view.phone = self.phone_input.value.strip()

        self.parent_view.update_button_states()
        embed = self.parent_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)

class CleanTicketInteractiveView(View):
    def __init__(
        self,
        role: Optional[str] = "ตำรวจ",
        first_name: Optional[str] = "Jelly",
        last_name: Optional[str] = "Walk",
        phone: Optional[str] = "123-456",
        photo_uploaded: bool = False
    ):
        super().__init__(timeout=None)
        self.role = role
        self.first_name = first_name
        self.last_name = last_name
        self.phone = phone
        self.photo_uploaded = photo_uploaded

        # Row 0: Dropdown
        options = [
            discord.SelectOption(label="Police", value="ตำรวจ", description="ตำรวจนครบาล (WUPD)", default=(role == "ตำรวจ")),
            discord.SelectOption(label="Medic", value="หมอ", description="หน่วยแพทย์ฉุกเฉิน (UMD)", default=(role == "หมอ"))
        ]
        self.role_select = Select(
            placeholder="Select Department (Police / Medic)...",
            options=options[:25],
            custom_id="clean_ticket_role_select",
            row=0
        )
        self.role_select.callback = self.select_role_callback
        self.add_item(self.role_select)

        # Row 1: All in one row!
        self.btn_info = Button(label="Fill Info", style=discord.ButtonStyle.secondary, custom_id="clean_ticket_fill_info", row=1)
        self.btn_info.callback = self.fill_info_callback
        self.add_item(self.btn_info)

        self.btn_submit = Button(label="Pending Info", style=discord.ButtonStyle.primary, disabled=True, custom_id="clean_ticket_submit", row=1)
        self.btn_submit.callback = self.submit_callback
        self.add_item(self.btn_submit)

        self.btn_close = Button(label="Close Ticket", style=discord.ButtonStyle.secondary, custom_id="clean_ticket_close", row=1)
        self.btn_close.callback = self.close_callback
        self.add_item(self.btn_close)

        self.update_button_states()

    def update_button_states(self):
        has_info = bool(self.first_name and self.last_name and self.phone)
        has_role = bool(self.role)
        is_medic = (self.role == "หมอ")
        photo_ok = True if is_medic else self.photo_uploaded

        is_ready = has_info and has_role and photo_ok
        self.btn_submit.disabled = not is_ready
        self.btn_submit.label = "Submit" if is_ready else "Pending Info"

    def build_embed(self) -> discord.Embed:
        c_ice = discord.Color.from_rgb(56, 182, 255)
        embed = discord.Embed(
            title="Registration Ticket #0042",
            description="-# กรุณากรอกข้อมูลให้ครบเพื่อรับ Role",
            color=c_ice
        )

        role_text = f"`{self.role}` (Selected)" if self.role else "`None`"
        embed.add_field(name="1. Department", value=role_text, inline=False)

        if self.first_name and self.last_name:
            info_text = f"`{self.first_name} {self.last_name}` • Phone: `{self.phone or '-'}`"
        else:
            info_text = "`Pending` (กดปุ่ม Fill Info ด้านล่าง)"
        embed.add_field(name="2. Character Info (IC)", value=info_text, inline=False)

        if self.role == "หมอ":
            photo_text = "`ไม่ต้องส่งรูป` (ยกเว้นสำหรับหมอ)"
        elif self.photo_uploaded:
            photo_text = "`Attached` (แนบรูปแล้ว)"
        else:
            photo_text = "`Pending` (แนบรูปส่งในห้องนี้ได้เลย)"
        embed.add_field(name="3. Story / Photo", value=photo_text, inline=False)

        embed.set_footer(text="ระบบจะปลดล็อคปุ่ม Submit อัตโนมัติเมื่อข้อมูลครบ")
        embed.set_thumbnail(url=WELCOME_BANNER_GIF)
        return embed

    async def select_role_callback(self, interaction: discord.Interaction):
        self.role = interaction.data["values"][0]
        for opt in self.role_select.options:
            opt.default = (opt.value == self.role)
        self.update_button_states()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def fill_info_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CleanCombinedInfoModal(parent_view=self))

    async def submit_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "**Submitted Successfully**\n-# ระบบได้บันทึกข้อมูลและส่งให้แอดมินเรียบร้อยแล้วครับ",
            ephemeral=True
        )

    async def close_callback(self, interaction: discord.Interaction):
        confirm_embed = discord.Embed(
            title="Confirm Close Ticket",
            description="คุณต้องการปิด Ticket นี้ใช่หรือไม่?\nห้องนี้และข้อมูลจะถูกลบออกถาวร",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=confirm_embed, view=CleanCloseConfirmView(), ephemeral=True)


# ==============================================================================
# 3. Leave Components (Breaktime)
# ==============================================================================

class CleanLeaveRequestModal(Modal):
    def __init__(self):
        super().__init__(title="ยื่นคำขอลางาน")
        self.start_date = TextInput(
            label="วันเริ่มลา (Start Date)",
            placeholder="เช่น วันนี้ หรือ 29/10/2026",
            required=True,
            max_length=20
        )
        self.days_count = TextInput(
            label="จำนวนวันที่ต้องการลา (วัน)",
            placeholder="เช่น 1, 3, 5, 7",
            required=True,
            max_length=3
        )
        self.reason = TextInput(
            label="เหตุผลการลา",
            placeholder="เช่น ติดธุระส่วนตัว / ไปต่างจังหวัด",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=200
        )
        self.add_item(self.start_date)
        self.add_item(self.days_count)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"**ยื่นคำขอลางานเรียบร้อยแล้ว**\n"
            f"• วันเริ่มลา: `{self.start_date.value.strip()}`\n"
            f"• จำนวนวัน: `{self.days_count.value.strip()} วัน`\n"
            f"• เหตุผล: `{self.reason.value.strip()}`\n\n"
            f"-# ระบบตรวจสอบโควตาจำนวนคนลาในวันนั้นแล้ว (ยังไม่เต็ม) บันทึกข้อมูลสำเร็จครับ",
            ephemeral=True
        )

class CleanLeavePanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ยื่นลา", style=discord.ButtonStyle.primary, custom_id="clean_leave_request")
    async def request_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(CleanLeaveRequestModal())

    @discord.ui.button(label="เช็คสิทธิ์", style=discord.ButtonStyle.secondary, custom_id="clean_leave_check")
    async def check_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="สิทธิ์การลางานของคุณ",
            description=f"**ผู้ใช้งาน:** {interaction.user.mention}\n-# ตรวจสอบโควตาและสถานะการลา",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.add_field(name="สถานะ", value="`ปกติ` (สามารถยื่นลาได้)", inline=True)
        embed.add_field(name="โควตาวันนี้", value="`2 / 5` คน (ว่าง 3 ที่)", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# UI Preview Cog
# ==============================================================================

class UIPreviewCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _check_admin(self, interaction: discord.Interaction) -> bool:
        return config.is_owner(interaction.user.id)

    def _get_preview_channel(self) -> Optional[discord.TextChannel]:
        ch_id = config.get("UI_PREVIEW_CHANNEL_ID")
        if not ch_id:
            return None
        return self.bot.get_channel(int(ch_id))

    async def _execute_preview(self, interaction: discord.Interaction, component: str):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        target_ch = self._get_preview_channel()
        if not target_ch:
            return await interaction.response.send_message(
                f"❌ ไม่พบห้อง Preview (Channel ID: `{config.get('UI_PREVIEW_CHANNEL_ID')}`)",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        now_ts = int(datetime.now().timestamp())
        now_dt = datetime.now(config.timezone)
        try:
            from cogs.operations import get_next_auto_stop_ts
            auto_stop_ts = get_next_auto_stop_ts() or int((now_dt + timedelta(minutes=45)).timestamp())
        except Exception:
            auto_stop_ts = int((datetime.now() + timedelta(minutes=45)).timestamp())

        c_slate = discord.Color.from_rgb(47, 49, 54)     # Deep Slate Dark
        c_blurple = discord.Color.from_rgb(88, 101, 242) # Discord Blurple
        c_ice = discord.Color.from_rgb(56, 182, 255)     # Clean Ice Blue
        c_red = discord.Color.from_rgb(237, 66, 69)      # Soft Crimson Alert

        # ======================================================================
        # 1. TIME TRACKER & OPERATIONS
        # ======================================================================
        if component in ["all", "operations"]:
            await target_ch.send("## ━━━ 1. TIME TRACKER & OPERATIONS SUITE ━━━")

            # 1.1 Main Panel
            extra_fields = [
                {"name": "สถานะระบบ", "value": "`Active` (เปิดรับลงเวลา)", "inline": True},
                {"name": "ตัดรอบอัตโนมัติ", "value": f"<t:{auto_stop_ts}:t> (<t:{auto_stop_ts}:R>)", "inline": True},
                {"name": "รหัส OTP", "value": f"ดูรหัสรอบปัจจุบันได้ที่ห้อง <#{config.get('OPERATION_OTP_CHANNEL')}>", "inline": False},
                {"name": "หน่วยปฏิบัติการพิเศษ (SWAT) [1]", "value": f"• {interaction.user.display_name} ({interaction.user.mention}) — <t:{now_ts - 3600}:R>", "inline": False},
                {"name": "ตำรวจนครบาล (Patrol) [1]", "value": f"• Sarah Connor (<@123456789012345678>) — <t:{now_ts - 1800}:R>", "inline": False},
                {"name": "กองบินตำรวจ (Air Support) [1]", "value": f"• Maverick Mitchell (<@234567890123456789>) — <t:{now_ts - 600}:R>", "inline": False}
            ]
            op_embeds = build_roster_embeds(
                title="WHAT UNIVERSAL — TIME TRACKER",
                description="-# ระบบลงเวลาทำงานและตรวจสอบรายชื่อคนออนไลน์",
                color=c_blurple,
                roster_lines=None,
                footer_text="What Universal • อัปเดตข้อมูลอัตโนมัติ",
                thumbnail_url=None,
                extra_fields=extra_fields
            )
            await target_ch.send(
                content="### 1.1 Main Time Tracker Panel (แผงบันทึกเวลาหลัก)",
                embeds=op_embeds,
                view=CleanOperationView()
            )

            # 1.2 OTP Passcode Card (ห้อง #otp-channel)
            otp_embed = discord.Embed(
                title="SECURITY PASSCODE (OTP)",
                description="```\n4821\n```\n-# รหัส 4 หลักสำหรับ Check-in เริ่มต้นทำงาน",
                color=c_blurple
            )
            otp_embed.add_field(name="Status", value="`Ready` (พร้อมใช้งาน)", inline=True)
            otp_embed.add_field(name="Auto-Refresh", value=f"<t:{now_ts + 1200}:R>", inline=True)
            otp_embed.set_footer(text="What Universal • OTP Security")
            await target_ch.send(
                content="### 1.2 OTP Passcode Broadcast (การ์ดรหัส OTP ในห้อง OTP)",
                embed=otp_embed
            )

            # 1.3 Personal Log Receipt (Check-in)
            receipt_in = discord.Embed(
                title="TIME LOG — CHECK-IN RECEIPT",
                description=(
                    f"**User:** {interaction.user.display_name} ({interaction.user.mention})\n"
                    f"• Timestamp: <t:{now_ts}:F>\n"
                    f"• Status: `Online` (เริ่มบันทึกเวลา)\n\n"
                    f"-# บันทึกข้อมูลลงประวัติส่วนบุคคลเรียบร้อยแล้ว"
                ),
                color=c_blurple
            )
            await target_ch.send(
                content="### 1.3 Personal Log Receipt: Check-in (ใบเสร็จส่วนบุคคลตอนเริ่มงาน)",
                embed=receipt_in
            )

            # 1.4 Personal Log Receipt (Check-out)
            receipt_out = discord.Embed(
                title="TIME LOG — CHECK-OUT RECEIPT",
                description=(
                    f"**User:** {interaction.user.display_name} ({interaction.user.mention})\n"
                    f"• Check-in: `10:00:00`\n"
                    f"• Check-out: `{now_dt.strftime('%H:%M:%S')}`\n"
                    f"• Duration: `2 ชม. 15 นาที`\n\n"
                    f"-# สรุปยอดเวลาทำงานประจำรอบเรียบร้อยแล้ว"
                ),
                color=c_blurple
            )
            await target_ch.send(
                content="### 1.4 Personal Log Receipt: Check-out (ใบเสร็จส่วนบุคคลตอนจบงาน)",
                embed=receipt_out
            )

            # 1.5 Auto-Stop Warning Announcement
            warn_embed = discord.Embed(
                title="AUTO-STOP ANNOUNCEMENT",
                description=(
                    f"ระบบจะทำการตัดรอบเวลาทำงานอัตโนมัติในอีก **10 นาที**\n"
                    f"• เวลาตัดรอบ: <t:{auto_stop_ts}:t> (<t:{auto_stop_ts}:R>)\n\n"
                    f"-# กรุณาเคลียร์งานที่ค้างอยู่ให้เรียบร้อยก่อนระบบตัดรอบ"
                ),
                color=c_red
            )
            warn_embed.set_footer(text="What Universal • Auto Termination Service")
            await target_ch.send(
                content="### 1.5 Auto-Stop Warning (ประกาศเตือน 10 นาทีก่อนตัดรอบอัตโนมัติ)",
                embed=warn_embed
            )

        # ======================================================================
        # 2. WELCOME & TICKET SYSTEM
        # ======================================================================
        if component in ["all", "welcome"]:
            await target_ch.send("## ━━━ 2. WELCOME & TICKET ONBOARDING SUITE ━━━")

            # 2.1 Welcome Panel (Banner at bottom)
            welcome_panel_embed = discord.Embed(
                title="WHAT UNIVERSAL — REGISTRATION",
                description=(
                    "-# ระบบลงทะเบียนและยืนยันตัวตนสมาชิกใหม่\n\n"
                    "ยินดีต้อนรับสู่ What Universal\n"
                    "กดปุ่ม **Open Ticket** ด้านล่างเพื่อเริ่มลงทะเบียน"
                ),
                color=c_slate
            )
            welcome_panel_embed.set_footer(text="Automated Registration • 24/7")
            welcome_panel_embed.set_image(url=WELCOME_BANNER_GIF)

            btn_open_ticket = Button(label="Open Ticket", style=discord.ButtonStyle.primary)
            v_welcome = View()
            v_welcome.add_item(btn_open_ticket)
            await target_ch.send(
                content="### 2.1 Welcome Panel (แผงหน้าห้องกดรับตั๋ว พร้อมภาพแบนเนอร์ด้านล่าง)",
                embed=welcome_panel_embed,
                view=v_welcome
            )

            # 2.2 Inside Ticket (Realtime Sync & Thumbnail GIF top right)
            active_ticket_view = CleanTicketInteractiveView(
                role="ตำรวจ",
                first_name="Jelly",
                last_name="Walk",
                phone="123-456",
                photo_uploaded=False
            )
            await target_ch.send(
                content="### 2.2 Inside Ticket (หน้าจอในห้องตั๋ว อัปเดตข้อมูลเรียลไทม์ ปุ่มแถวเดียวกัน)",
                embed=active_ticket_view.build_embed(),
                view=active_ticket_view
            )

            # 2.3 Close Ticket Confirm Popup
            confirm_preview = discord.Embed(
                title="Confirm Close Ticket",
                description="คุณต้องการปิด Ticket นี้ใช่หรือไม่?\nห้องนี้และข้อมูลจะถูกลบออกถาวร",
                color=c_red
            )
            await target_ch.send(
                content="### 2.3 Close Ticket Confirmation (หน้าต่างยืนยันปิดห้องตั๋ว พร้อมปุ่มสีแดง)",
                embed=confirm_preview,
                view=CleanCloseConfirmView()
            )

            # 2.4 Data Log Archive (ห้อง #welcome-data-log)
            archive_embed = discord.Embed(
                title="REGISTRATION DOSSIER — JELLY WALK",
                description=(
                    f"**Member:** {interaction.user.display_name} ({interaction.user.mention})\n"
                    f"• First Name: `Jelly`\n"
                    f"• Last Name: `Walk`\n"
                    f"• In-game Phone: `123-456`\n"
                    f"• Department: `Police`\n"
                    f"• Assigned Role: `@ตำรวจนครบาล`\n"
                    f"• Completed At: <t:{now_ts}:F>\n\n"
                    f"-# ข้อมูลได้รับการตรวจสอบและจัดเก็บเข้าฐานข้อมูลเรียบร้อยแล้ว"
                ),
                color=c_ice,
                timestamp=now_dt
            )
            archive_embed.set_footer(text="What Universal Onboarding Registry")
            await target_ch.send(
                content="### 2.4 Data Log Archive (การ์ดบันทึกประวัติผู้เล่นเข้าห้อง Log เมื่อยืนยันเสร็จ)",
                embed=archive_embed
            )

            # 2.5 Member Join Card (ห้อง #welcome-join-log)
            join_embed = discord.Embed(
                title="MEMBER JOINED",
                description=(
                    f"ยินดีต้อนรับ {interaction.user.display_name} ({interaction.user.mention}) เข้าสู่ **What Universal**\n"
                    f"• Username: `{interaction.user.name}`\n"
                    f"• Account Age: <t:{int(interaction.user.created_at.timestamp())}:R>\n\n"
                    f"-# กรุณาไปยังห้อง <#{config.get('WELCOME_TICKET_CHANNEL_ID')}> เพื่อเปิด Ticket ลงทะเบียน"
                ),
                color=c_blurple
            )
            join_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await target_ch.send(
                content="### 2.5 Member Join Notification (แจ้งเตือนสมาชิกใหม่เข้าดิสคอร์ด)",
                embed=join_embed
            )

            # 2.6 Member Leave Card (ห้อง #welcome-join-log)
            leave_log_embed = discord.Embed(
                title="MEMBER LEFT",
                description=(
                    f"{interaction.user.display_name} ({interaction.user.mention}) ได้ออกจากเซิร์ฟเวอร์\n"
                    f"• Username: `{interaction.user.name}`\n"
                    f"• Joined: <t:{now_ts - 86400 * 45}:R>\n\n"
                    f"-# ระบบบันทึกข้อมูลการออกจากเซิร์ฟเวอร์เรียบร้อยแล้ว"
                ),
                color=c_slate
            )
            leave_log_embed.set_thumbnail(url=interaction.user.display_avatar.url)
            await target_ch.send(
                content="### 2.6 Member Leave Notification (แจ้งเตือนสมาชิกออกจากดิสคอร์ด)",
                embed=leave_log_embed
            )

            # 2.7 Temporary Invite Link DM Card (/generate_link)
            invite_dm = discord.Embed(
                title="INVITATION LINK",
                description=(
                    f"ลิงก์เชิญเข้าร่วมเซิร์ฟเวอร์ **What Universal** สำหรับคุณ:\n\n"
                    f"🔗 `https://discord.gg/what-universal-sample`\n\n"
                    f"• จำนวนการใช้งาน: `1 ครั้ง`\n"
                    f"• อายุลิงก์: `30 นาที`\n\n"
                    f"-# ข้อความนี้จะถูกลบอัตโนมัติภายใน 1 ชั่วโมงเพื่อความปลอดภัย"
                ),
                color=c_blurple
            )
            invite_dm.set_footer(text="What Universal Security Link Service")
            await target_ch.send(
                content="### 2.7 Invite Link DM Receipt (การ์ดส่งลิงก์เชิญชั่วคราวเข้า DM)",
                embed=invite_dm
            )

        # ======================================================================
        # 3. LEAVE BOARD & REQUEST (BREAKTIME)
        # ======================================================================
        if component in ["all", "leave"]:
            await target_ch.send("## ━━━ 3. LEAVE BOARD & REQUEST SUITE ━━━")

            leave_extra = [
                {
                    "name": "โควตาวันนี้",
                    "value": "ใช้ไป `2 / 5` คน (ว่างอีก `3` ที่)",
                    "inline": False
                },
                {
                    "name": "คนที่ลาล่วงหน้า",
                    "value": f"• `[AIR]` Thomas Wayne — ลา 4 วัน (<t:{now_ts + 86400 * 2}:D> — <t:{now_ts + 86400 * 6}:D>)",
                    "inline": False
                }
            ]
            active_leaves = [
                f"• `[SWAT]` Alex Mercer — สิ้นสุด <t:{now_ts + 86400 * 3}:D> (เหลือ 3 วัน)",
                f"• `[POL]` Diana Prince — สิ้นสุด <t:{now_ts + 86400}:D> (เหลือ 1 วัน)"
            ]
            leave_board_embeds = build_roster_embeds(
                title="WHAT UNIVERSAL — LEAVE BOARD",
                description="-# รายงานสถานะวันลาและตรวจสอบโควตาประจำวัน",
                color=c_slate,
                roster_lines=active_leaves,
                section_name="คนที่กำลังลาอยู่",
                items_per_field=10,
                footer_text="ตัดรอบวันลาทุก 03:00 น.",
                extra_fields=leave_extra
            )
            await target_ch.send(
                content="### 3.1 Unified Leave Board (กระดานวันลารวมข้อความเดียว + ปุ่มยื่นลา/เช็คสิทธิ์)",
                embeds=leave_board_embeds,
                view=CleanLeavePanelView()
            )

            leave_audit = discord.Embed(
                title="AUDIT LOG — LEAVE REQUEST FILED",
                color=c_slate,
                timestamp=now_dt
            )
            leave_audit.add_field(name="User", value=f"{interaction.user.display_name} ({interaction.user.mention})", inline=True)
            leave_audit.add_field(name="Duration", value="`3 วัน`", inline=True)
            leave_audit.add_field(name="Period", value=f"<t:{now_ts}:D> — <t:{now_ts + 86400 * 3}:D>", inline=False)
            leave_audit.add_field(name="Reason", value="`ติดธุระส่วนตัวต่างจังหวัด`", inline=False)
            leave_audit.set_footer(text="Leave Registry Audit")
            await target_ch.send(
                content="### 3.2 Leave Request Audit Log (การ์ดส่งเข้าห้อง Log เมื่อมีคนยื่นลา)",
                embed=leave_audit
            )

        # ======================================================================
        # 4. CONFIG DASHBOARD
        # ======================================================================
        if component in ["all", "config"]:
            await target_ch.send("## ━━━ 4. SYSTEM CONFIGURATION DASHBOARD ━━━")

            cfg_embed = discord.Embed(
                title="WHAT UNIVERSAL — SYSTEM CONFIG",
                description="-# ศูนย์กลางการตั้งค่าระบบและสถานะการทำงานแบบ Realtime",
                color=c_blurple
            )
            cfg_embed.add_field(
                name="Server & Environment",
                value=f"• Guild ID: `{config.guild_id}`\n• Timezone: `{config.timezone}`\n• Version: `{config.get('BOT_VERSION')}`",
                inline=False
            )
            cfg_embed.add_field(
                name="Channels & Routing",
                value=(
                    f"• Time Tracker Log: <#{config.get('OPERATION_LOG_CHANNEL_ID')}>\n"
                    f"• OTP Passcode: <#{config.get('OPERATION_OTP_CHANNEL')}>\n"
                    f"• Ticket Panel: <#{config.get('WELCOME_TICKET_CHANNEL_ID')}>\n"
                    f"• Ticket Data Log: <#{config.get('WELCOME_DATA_LOG_CHANNEL_ID')}>\n"
                    f"• Leave Board: <#{config.get('LEAVE_BOARD_CHANNEL_ID')}>\n"
                    f"• Preview Channel: <#{config.get('UI_PREVIEW_CHANNEL_ID')}>"
                ),
                inline=False
            )
            cfg_embed.set_footer(text="All settings update in-memory without restart")
            await target_ch.send(
                content="### 4.1 System Config Dashboard (แผงตั้งค่าและภาพรวมคอนฟิก)",
                embed=cfg_embed
            )

        # ======================================================================
        # 5. SPLIT ENGINE STRESS TEST
        # ======================================================================
        if component in ["all", "split_demo"]:
            await target_ch.send("## ━━━ 5. SPLIT ENGINE DEMONSTRATION ━━━")

            simulated_officers = [
                f"• `[SWAT]` User Alpha {i:02d} — <t:{now_ts - (i * 120)}:R>"
                for i in range(1, 26)
            ]
            split_extra = [
                {"name": "สถานะระบบ", "value": "`เปิดรับลงเวลาปกติ`", "inline": True},
                {"name": "จำนวนคนทำงานทั้งหมด", "value": f"`{len(simulated_officers)} คน`", "inline": True}
            ]
            split_embeds = build_roster_embeds(
                title="WHAT UNIVERSAL — TIME TRACKER (SPLIT ENGINE TEST)",
                description="-# จำลองคน Check-in 25 คน • แสดงการแบ่งช่องและแบ่งหน้าอัตโนมัติ",
                color=c_blurple,
                roster_lines=simulated_officers,
                section_name="รายชื่อคนที่ Check-in อยู่",
                items_per_field=10,
                footer_text="ทดสอบระบบแบ่ง Embed อัตโนมัติ",
                thumbnail_url=None,
                extra_fields=split_extra
            )
            await target_ch.send(
                content="### 5.1 Split Engine Stress Test (จำลองคน Check-in 25 คนพร้อมกัน ป้องกัน Embed ล้น)",
                embeds=split_embeds,
                view=CleanOperationView()
            )

        await interaction.followup.send(
            f"✅ ส่งตัวอย่าง UI ทุกส่วนไปยังห้อง <#{target_ch.id}> เรียบร้อยแล้ว!\n"
            f"• หมวดที่ส่ง: `{component}`\n"
            f"• ดีไซน์: Clean Gen-Z Minimalist โทนเดียวกันทั้งระบบ\n"
            f"• คุณสามารถเปิดดูและคลิกทดสอบปุ่มทุกอันได้ทันที",
            ephemeral=True
        )

    @app_commands.command(
        name="preview",
        description="[Admin] ส่งตัวอย่างหน้าตา UI ทุกส่วนที่เกี่ยวข้องไปยังห้อง Preview เพื่อตรวจดูภาพรวม"
    )
    @app_commands.describe(
        component="ส่วนที่ต้องการดูตัวอย่าง (เลือก 'all' เพื่อดูหน้าจอทั้งหมดของบอท)"
    )
    @app_commands.choices(component=[
        app_commands.Choice(name="ทั้งหมดทุกส่วน (All System Components)", value="all"),
        app_commands.Choice(name="1. ระบบบันทึกเวลา (Time Tracker & Operations)", value="operations"),
        app_commands.Choice(name="2. ระบบต้อนรับ & Ticket (Welcome & Ticket Suite)", value="welcome"),
        app_commands.Choice(name="3. ระบบลางาน (Leave Board Suite)", value="leave"),
        app_commands.Choice(name="4. แผงควบคุมการตั้งค่า (Config Dashboard)", value="config"),
        app_commands.Choice(name="5. ทดสอบระบบแบ่งหน้า (Split Engine Demo)", value="split_demo")
    ])
    async def preview_command(self, interaction: discord.Interaction, component: str = "all"):
        await self._execute_preview(interaction, component)

    @app_commands.command(
        name="preview_ui",
        description="[Admin] คำสั่งทางลัดสำหรับดูหน้าตา UI (ทำงานเหมือนคำสั่ง /preview)"
    )
    @app_commands.describe(component="ส่วนที่ต้องการดูตัวอย่าง")
    @app_commands.choices(component=[
        app_commands.Choice(name="ทั้งหมดทุกส่วน (All System Components)", value="all"),
        app_commands.Choice(name="1. ระบบบันทึกเวลา (Time Tracker & Operations)", value="operations"),
        app_commands.Choice(name="2. ระบบต้อนรับ & Ticket (Welcome & Ticket Suite)", value="welcome"),
        app_commands.Choice(name="3. ระบบลางาน (Leave Board Suite)", value="leave"),
        app_commands.Choice(name="4. แผงควบคุมการตั้งค่า (Config Dashboard)", value="config"),
        app_commands.Choice(name="5. ทดสอบระบบแบ่งหน้า (Split Engine Demo)", value="split_demo")
    ])
    async def preview_ui_command(self, interaction: discord.Interaction, component: str = "all"):
        await self._execute_preview(interaction, component)

    @app_commands.command(
        name="preview_clean",
        description="[Admin] ลบข้อความตัวอย่างที่ส่งทดสอบในห้อง Preview ออกทั้งหมด"
    )
    async def preview_clean_command(self, interaction: discord.Interaction):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        target_ch = self._get_preview_channel()
        if not target_ch:
            return await interaction.response.send_message(
                f"❌ ไม่พบห้อง Preview (Channel ID: `{config.get('UI_PREVIEW_CHANNEL_ID')}`)",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await target_ch.purge(limit=100, check=lambda m: m.author == self.bot.user)
            await interaction.followup.send(
                f"🧹 ลบข้อความตัวอย่างของบอทในห้อง <#{target_ch.id}> ออกแล้วทั้งหมด `{len(deleted)}` ข้อความ",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดในการลบข้อความ: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(UIPreviewCog(bot))
