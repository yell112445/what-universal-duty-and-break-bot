import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Button, Select
from datetime import datetime, timedelta
from typing import Optional, List
from config_manager import config

# ==============================================================================
# Helper: Clean & Robust Embed Split Engine (ป้องกัน Embed ล้น 100%)
# ==============================================================================

def chunk_list(items: list, chunk_size: int):
    """แบ่ง list ออกเป็นก้อนย่อยๆ ตามขนาด chunk_size"""
    for i in range(0, len(items), chunk_size):
        yield items[i:i + chunk_size]

def build_roster_embeds(
    title: str,
    description: str,
    color: discord.Color,
    roster_lines: list[str],
    section_name: str = "รายชื่อคนที่ Check-in อยู่",
    items_per_field: int = 10,
    footer_text: str = "What Universal • อัปเดตข้อมูลอัตโนมัติ",
    thumbnail_url: Optional[str] = None,
    extra_fields: Optional[list[dict]] = None
) -> list[discord.Embed]:
    """
    สร้าง Discord Embeds โดยคิดเผื่อกรณีข้อมูลยาวเกินกำหนด (Split Engine):
    - ตัดทอนให้อยู่ใน Field ไม่เกิน 1024 ตัวอักษร (แบ่ง Field 1-10, 11-20...)
    - ถ้าจำนวนคนเยอะมากเกิน 20 คน จะแยกเป็น Multi-Embed (Chained Embeds) อัตโนมัติ
    """
    if not roster_lines:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )
        if extra_fields:
            for f in extra_fields:
                embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
        embed.add_field(name=section_name, value="*— ไม่มีรายชื่อในขณะนี้ —*", inline=False)
        embed.set_footer(text=footer_text)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        return [embed]

    chunks = list(chunk_list(roster_lines, items_per_field))
    embeds = []

    # ก้อนที่ 1: Embed หลัก (ใส่ Description + Extra Fields + Roster ชุดแรก)
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

    # ถ้ามีมากกว่า 2 Chunks (> 20 คน) -> Split เป็น Chained Embed แผ่นที่ 2 ต่อเนื่อง
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
# 1. Clean Operations System (ปุ่มฟ้า/เทา/เทา — ไม่มีสีแดง, ศัพท์ทั่วไป)
# ==============================================================================

class CleanOTPModal(Modal):
    def __init__(self):
        super().__init__(title="ยืนยันรหัส OTP")
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
    @discord.ui.button(
        label="Check-in",
        style=discord.ButtonStyle.primary,
        custom_id="clean_op_checkin"
    )
    async def checkin_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(CleanOTPModal())

    # ปุ่ม 2: เทา (Secondary) - เอาสีแดงออก เปลี่ยนเป็นเทา
    @discord.ui.button(
        label="Check-out",
        style=discord.ButtonStyle.secondary,
        custom_id="clean_op_checkout"
    )
    async def checkout_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="CHECK-OUT SUCCESS",
            description=(
                f"**ผู้ใช้งาน:** {interaction.user.mention}\n"
                f"• Check-in: `10:00:00`\n"
                f"• Check-out: `{datetime.now(config.timezone).strftime('%H:%M:%S')}`\n"
                f"• รวมเวลาทำงาน: `2 ชม. 15 นาที`\n\n"
                f"-# ระบบบันทึกข้อมูลเรียบร้อยแล้ว ขอบคุณสำหรับการทำงานครับ"
            ),
            color=discord.Color.from_rgb(88, 101, 242) # Blurple
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ปุ่ม 3: เทา (Secondary) - ตรวจสอบสถานะ
    @discord.ui.button(
        label="My Status",
        style=discord.ButtonStyle.secondary,
        custom_id="clean_op_status"
    )
    async def status_button(self, interaction: discord.Interaction, button: Button):
        now_ts = int(datetime.now().timestamp())
        embed = discord.Embed(
            title="STATUS REPORT",
            color=discord.Color.from_rgb(88, 101, 242) # Blurple
        )
        embed.add_field(name="สถานะปัจจุบัน", value="`Online` (กำลังทำงาน)", inline=True)
        embed.add_field(name="เริ่มงานเมื่อ", value=f"<t:{now_ts - 3600}:R>", inline=True)
        embed.add_field(name="เวลารวมวันนี้", value="`3 ชั่วโมง 40 นาที`", inline=False)
        embed.set_footer(text="What Universal • ซิงค์ข้อมูลเรียลไทม์")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# 2. Clean Welcome Ticket System (ปุ่มฟ้า/เทา, แดงเฉพาะปิด Ticket, เรียลไทม์)
# ==============================================================================

WELCOME_BANNER_GIF = "https://media.discordapp.net/attachments/752095905074642945/1341411365839175710/Final.gif?ex=67b5e62f&is=67b494af&hm=79d36fd5a51ec8e8f09ff36628b92d56682a6e359816404b5dd57591530b400f&=&width=578&height=578"

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
        # แสดงผลอัปเดตข้อมูลบน Panel ทันทีแบบเรียลไทม์!
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

        # Row 0: Dropdown แผนก (มีเฉพาะ ตำรวจ และ หมอ)
        options = [
            discord.SelectOption(label="Police", value="ตำรวจ", description="ตำรวจนครบาล (WUPD)", default=(role == "ตำรวจ")),
            discord.SelectOption(label="Medic", value="หมอ", description="หน่วยแพทย์ฉุกเฉิน (UMD)", default=(role == "หมอ"))
        ]
        self.role_select = Select(
            placeholder="Select Department (Police / Medic)...",
            options=options,
            custom_id="clean_ticket_role_select",
            row=0
        )
        self.role_select.callback = self.select_role_callback
        self.add_item(self.role_select)

        # Row 1: ปุ่มทั้งหมดอยู่ในแถวเดียวกัน! สีเทา/ฟ้า/เทา (ไม่มีสีแดงในแถวนี้)
        # ปุ่ม 1: กรอกข้อมูลตัวละคร (สีเทา)
        self.btn_info = Button(
            label="Fill Info",
            style=discord.ButtonStyle.secondary,
            custom_id="clean_ticket_fill_info",
            row=1
        )
        self.btn_info.callback = self.fill_info_callback
        self.add_item(self.btn_info)

        # ปุ่ม 2: ยืนยันข้อมูล (สีฟ้า / ล็อคจนกว่าจะครบ)
        self.btn_submit = Button(
            label="Pending Info",
            style=discord.ButtonStyle.primary,
            disabled=True,
            custom_id="clean_ticket_submit",
            row=1
        )
        self.btn_submit.callback = self.submit_callback
        self.add_item(self.btn_submit)

        # ปุ่ม 3: ปิด Ticket (สีเทา ในแถวเดียวกัน!)
        self.btn_close = Button(
            label="Close Ticket",
            style=discord.ButtonStyle.secondary,
            custom_id="clean_ticket_close",
            row=1
        )
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

        # 1. Department
        role_text = f"`{self.role}` (Selected)" if self.role else "`None`"
        embed.add_field(name="1. Department", value=role_text, inline=False)

        # 2. Character Info (IC)
        if self.first_name and self.last_name:
            info_text = f"`{self.first_name} {self.last_name}` • Phone: `{self.phone or '-'}`"
        else:
            info_text = "`Pending` (กดปุ่ม Fill Info ด้านล่าง)"
        embed.add_field(name="2. Character Info (IC)", value=info_text, inline=False)

        # 3. Story / Photo
        if self.role == "หมอ":
            photo_text = "`ไม่ต้องส่งรูป` (ยกเว้นสำหรับหมอ)"
        elif self.photo_uploaded:
            photo_text = "`Attached` (แนบรูปแล้ว)"
        else:
            photo_text = "`Pending` (แนบรูปส่งในห้องนี้ได้เลย)"
        embed.add_field(name="3. Story / Photo", value=photo_text, inline=False)

        embed.set_footer(text="ระบบจะปลดล็อคปุ่ม Submit อัตโนมัติเมื่อข้อมูลครบ")
        # 2.2 เป็นภาพมุมขวาบน (Thumbnail)
        embed.set_thumbnail(url=WELCOME_BANNER_GIF)
        return embed

    async def select_role_callback(self, interaction: discord.Interaction):
        self.role = interaction.data["values"][0]
        for opt in self.role_select.options:
            opt.default = (opt.value == self.role)
        self.update_button_states()
        # อัปเดต Embed ทันทีแบบเรียลไทม์
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def fill_info_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CleanCombinedInfoModal(parent_view=self))

    async def submit_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "**Submitted Successfully**\n-# ระบบได้บันทึกข้อมูลและส่งให้แอดมินเรียบร้อยแล้วครับ",
            ephemeral=True
        )

    async def close_callback(self, interaction: discord.Interaction):
        # เมื่อกดปุ่ม Close Ticket (สีเทา) -> เด้งถาม Confirm พร้อมปุ่ม "Confirm Close" สีแดง
        confirm_embed = discord.Embed(
            title="Confirm Close Ticket",
            description="คุณต้องการปิด Ticket นี้ใช่หรือไม่?\nห้องนี้และข้อมูลจะถูกลบออกถาวร",
            color=discord.Color.red()
        )
        await interaction.response.send_message(
            embed=confirm_embed,
            view=CleanCloseConfirmView(),
            ephemeral=True
        )


# ==============================================================================
# 3. Clean Leave System (ปุ่มฟ้า/เทา/เทา — ศัพท์ทั่วไป)
# ==============================================================================

class CleanLeavePanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    # ปุ่ม 1: ฟ้า (Primary)
    @discord.ui.button(
        label="ยื่นลา 7 วัน",
        style=discord.ButtonStyle.primary,
        custom_id="clean_leave_l7"
    )
    async def l7_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "-# เปิดหน้าต่างระบุวันที่เริ่มลา 7 วัน (ระบบจะคำนวณวันสิ้นสุดและเช็คโควตาให้อัตโนมัติ)",
            ephemeral=True
        )

    # ปุ่ม 2: เทา (Secondary)
    @discord.ui.button(
        label="ยื่นลา 3-4 วัน",
        style=discord.ButtonStyle.secondary,
        custom_id="clean_leave_l34"
    )
    async def l34_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "-# เปิดหน้าต่างระบุวันที่เริ่มลา 3-4 วัน (ระบบจะคำนวณวันสิ้นสุดและเช็คโควตาให้อัตโนมัติ)",
            ephemeral=True
        )

    # ปุ่ม 3: เทา (Secondary)
    @discord.ui.button(
        label="เช็คสิทธิ์วันลา",
        style=discord.ButtonStyle.secondary,
        custom_id="clean_leave_check"
    )
    async def check_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="สิทธิ์วันลาของคุณ",
            description=f"**ผู้ใช้งาน:** {interaction.user.mention}\n-# อัปเดตสิทธิ์คงเหลือประจำเดือน",
            color=discord.Color.from_rgb(88, 101, 242)
        )
        embed.add_field(name="โควตาลา 7 วัน", value="เหลือสิทธิ์ `1 ครั้ง`", inline=True)
        embed.add_field(name="โควตาลา 3-4 วัน", value="เหลือสิทธิ์ `2 ครั้ง`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# Main Preview Cog
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

    @app_commands.command(
        name="preview_ui",
        description="[Admin] ส่งตัวอย่างหน้าตา Clean Gen-Z UX/UI (ปุ่มฟ้า/เทา + Split Engine) เข้าห้อง Preview"
    )
    @app_commands.describe(
        component="ระบบที่ต้องการดูตัวอย่าง (เลือก 'all' หรือ 'split_demo' เพื่อทดสอบ Embed ล้น)"
    )
    @app_commands.choices(component=[
        app_commands.Choice(name="ทั้งหมด (All Systems - Clean Style)", value="all"),
        app_commands.Choice(name="1. ระบบลงเวลา (Time Tracker)", value="operation"),
        app_commands.Choice(name="2. ระบบลงทะเบียนสมาชิกใหม่ (Welcome & Ticket)", value="welcome"),
        app_commands.Choice(name="3. ระบบแจ้งวันลา (Leave Board)", value="leave"),
        app_commands.Choice(name="4. ทดสอบ Split Engine (จำลองคนทำงาน 25 คน)", value="split_demo")
    ])
    async def preview_ui_command(self, interaction: discord.Interaction, component: str = "all"):
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
        auto_stop_ts = int((datetime.now() + timedelta(minutes=45)).timestamp())

        # Palette colors (Clean Minimalist Theme)
        c_slate = discord.Color.from_rgb(47, 49, 54)     # Deep Slate Dark
        c_blurple = discord.Color.from_rgb(88, 101, 242) # Discord Blurple
        c_ice = discord.Color.from_rgb(56, 182, 255)     # Clean Ice Blue

        # ----------------------------------------------------------------------
        # 1. Operation Preview (Clean Gen-Z Style — ศัพท์ทั่วไป ไม่ใช้คำว่าเข้าเวร/ออกเวร)
        # ----------------------------------------------------------------------
        if component in ["all", "operation"]:
            extra_fields = [
                {"name": "สถานะระบบ", "value": "`Active` (เปิดรับลงเวลา)", "inline": True},
                {"name": "ตัดรอบอัตโนมัติ", "value": f"<t:{auto_stop_ts}:t> (<t:{auto_stop_ts}:R>)", "inline": True},
                {"name": "รหัส OTP", "value": f"ดูรหัสรอบปัจจุบันได้ที่ห้อง <#{config.get('OPERATION_OTP_CHANNEL')}>", "inline": False}
            ]

            active_officers = [
                f"• `[SWAT]` Jackson Teller — <t:{now_ts - 3600}:R>",
                f"• `[POL]` Sarah Connor — <t:{now_ts - 1800}:R>",
                f"• `[AIR]` Maverick Mitchell — <t:{now_ts - 600}:R>"
            ]

            op_embeds = build_roster_embeds(
                title="WHAT UNIVERSAL — TIME TRACKER",
                description="-# ระบบลงเวลาทำงานและตรวจสอบรายชื่อคนออนไลน์",
                color=c_blurple,
                roster_lines=active_officers,
                section_name="รายชื่อคนที่ Check-in อยู่",
                items_per_field=10,
                footer_text="What Universal • อัปเดตข้อมูลอัตโนมัติ",
                thumbnail_url=None,
                extra_fields=extra_fields
            )

            await target_ch.send(
                content="### ─── 01. ระบบลงเวลา (TIME TRACKER) ───",
                embeds=op_embeds,
                view=CleanOperationView()
            )

        # ----------------------------------------------------------------------
        # 2. Welcome & Ticket Preview (Clean Minimalist — Modern English)
        # ----------------------------------------------------------------------
        if component in ["all", "welcome"]:
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
            # 2.1 เป็นภาพแนบด้านล่าง (Banner) ไม่ใช่มุมขวาบน
            welcome_panel_embed.set_image(url=WELCOME_BANNER_GIF)

            btn_open_ticket = Button(
                label="Open Ticket",
                style=discord.ButtonStyle.primary
            )
            v_welcome = View()
            v_welcome.add_item(btn_open_ticket)

            await target_ch.send(
                content="### ─── 02.1 WELCOME PANEL ───",
                embed=welcome_panel_embed,
                view=v_welcome
            )

            # 2.2 หน้าจอภายในตั๋วของผู้เล่น (Active Ticket - อัปเดตข้อมูลเรียลไทม์)
            active_ticket_view = CleanTicketInteractiveView(
                role="ตำรวจ",
                first_name="Jelly",
                last_name="Walk",
                phone="123-456",
                photo_uploaded=False
            )

            await target_ch.send(
                content="### ─── 02.2 INSIDE TICKET ───",
                embed=active_ticket_view.build_embed(),
                view=active_ticket_view
            )

        # ----------------------------------------------------------------------
        # 3. Leave Board & Panel (Clean Minimalist — ศัพท์ทั่วไป)
        # ----------------------------------------------------------------------
        if component in ["all", "leave"]:
            leave_extra = [
                {
                    "name": "คนที่ลาล่วงหน้า",
                    "value": f"• `[AIR]` Thomas Wayne — ลา 4 วัน (<t:{now_ts + 86400 * 2}:D> — <t:{now_ts + 86400 * 6}:D>)",
                    "inline": False
                },
                {
                    "name": "โควตาการลาวันนี้",
                    "value": "ใช้ไป `2 / 5` คน (ว่างอีก `3` ที่)",
                    "inline": False
                }
            ]

            active_leaves = [
                f"• `[SWAT]` Alex Mercer — ลา 7 วัน (สิ้นสุด <t:{now_ts + 86400 * 3}:D> • เหลือ 3 วัน)",
                f"• `[POL]` Diana Prince — ลา 3 วัน (สิ้นสุด <t:{now_ts + 86400}:D> • เหลือ 1 วัน)"
            ]

            leave_board_embeds = build_roster_embeds(
                title="WHAT UNIVERSAL — กระดานแจ้งวันลา",
                description="-# รายชื่อคนที่กำลังลาและสิทธิ์วันลาคงเหลือ",
                color=c_slate,
                roster_lines=active_leaves,
                section_name="คนที่กำลังลาอยู่",
                items_per_field=10,
                footer_text="ตัดรอบวันลาทุก 03:00 น.",
                extra_fields=leave_extra
            )

            leave_panel_embed = discord.Embed(
                title="ระบบยื่นขอลาพักผ่อน",
                description=(
                    "-# เลือกประเภทการลาที่ต้องการ\n\n"
                    "• **ยื่นลา 7 วัน:** เหมาะสำหรับการลาพักผ่อนประจำเดือน\n"
                    "• **ยื่นลา 3-4 วัน:** เหมาะสำหรับธุระสั้นเร่งด่วน\n\n"
                    "-# กรุณาเช็คโควตาคงเหลือก่อนกดยื่นคำขอ"
                ),
                color=c_blurple
            )

            await target_ch.send(
                content="### ─── 03. กระดานวันลา & แผงยื่นคำขอ (LEAVE BOARD) ───",
                embeds=leave_board_embeds
            )
            await target_ch.send(
                embed=leave_panel_embed,
                view=CleanLeavePanelView()
            )

        # ----------------------------------------------------------------------
        # 4. Split Engine Demo (จำลองคนทำงาน 25 คน)
        # ----------------------------------------------------------------------
        if component in ["all", "split_demo"]:
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
                content="### ─── 04. ทดสอบระบบ SPLIT ENGINE (คนทำงาน 25 คน) ───",
                embeds=split_embeds,
                view=CleanOperationView()
            )

        await interaction.followup.send(
            f"✅ ส่งตัวอย่าง Clean Gen-Z UI Component ไปยังห้อง <#{target_ch.id}> เรียบร้อยแล้ว!\n"
            f"• ปุ่ม: Check-in (ฟ้า) | Check-out (เทา) | My Status (เทา)\n"
            f"• ภาษา: ศัพท์ทั่วไป เป็นกันเอง ไม่ใช้คำว่าเข้าเวร/ออกเวร\n"
            f"• สีแดง: สงวนไว้เฉพาะปุ่มสำคัญที่ลบข้อมูล/ปิดห้องเท่านั้น",
            ephemeral=True
        )

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
            deleted = await target_ch.purge(limit=50, check=lambda m: m.author == self.bot.user)
            await interaction.followup.send(
                f"🧹 ลบข้อความตัวอย่างของบอทในห้อง <#{target_ch.id}> ออกแล้วทั้งหมด `{len(deleted)}` ข้อความ",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดในการลบข้อความ: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(UIPreviewCog(bot))
