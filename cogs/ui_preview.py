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
        otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
        self.otp_input = TextInput(
            label="รหัส OTP",
            placeholder=f"ดูรหัส 4 หลักได้จากห้อง #{otp_ch_id}",
            required=True,
            min_length=4,
            max_length=6
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
# 2. Clean Welcome Ticket System (ปุ่มฟ้า/เทา, แดงเฉพาะปิดตั๋ว)
# ==============================================================================

class CleanCombinedInfoModal(Modal):
    def __init__(self):
        super().__init__(title="กรอกข้อมูลตัวละคร (IC)")
        self.name_input = TextInput(
            label="ชื่อ - นามสกุล ตัวละคร (IC)",
            placeholder="เช่น Somchai Jaidee",
            required=True,
            max_length=60
        )
        self.phone_input = TextInput(
            label="เบอร์โทรศัพท์ (IC)",
            placeholder="เช่น 081-234-5678 หรือ 123456",
            required=True,
            max_length=20
        )
        self.add_item(self.name_input)
        self.add_item(self.phone_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"**บันทึกข้อมูลสำเร็จ**\n"
            f"• ชื่อ: `{self.name_input.value.strip()}`\n"
            f"• เบอร์โทร: `{self.phone_input.value.strip()}`\n\n"
            f"-# กรุณาแนบรูปถ่ายสตอรี่หรือเอกสารในแชทนี้เพื่อปลดล็อคปุ่มยืนยัน",
            ephemeral=True
        )

class CleanTicketInteractiveView(View):
    def __init__(self, step: int = 1):
        super().__init__(timeout=None)
        self.step = step

        # Dropdown สายงาน
        options = [
            discord.SelectOption(label="Police", value="police", description="ตำรวจนครบาล"),
            discord.SelectOption(label="Cadet", value="beginner", description="นักเรียนตำรวจฝึกหัด"),
            discord.SelectOption(label="Medic", value="md", description="หมอ / หน่วยแพทย์"),
            discord.SelectOption(label="Assistant", value="assistant", description="ผู้ช่วย / ประสานงาน")
        ]
        select = Select(
            placeholder="เลือกแผนก / ตำแหน่ง...",
            options=options,
            custom_id="clean_ticket_role_select",
            row=0
        )
        select.callback = self.select_role_callback
        self.add_item(select)

        # ปุ่ม 1: เทา (Secondary) - กรอกข้อมูล
        btn_info = Button(
            label="กรอกข้อมูลตัวละคร",
            style=discord.ButtonStyle.secondary,
            custom_id="clean_ticket_fill_info",
            row=1
        )
        btn_info.callback = self.fill_info_callback
        self.add_item(btn_info)

        # ปุ่ม 2: ฟ้า (Primary) - ยืนยันส่งเรื่อง
        is_ready = (step >= 3)
        btn_submit = Button(
            label="ยืนยันข้อมูล" if is_ready else "รอข้อมูลครบ",
            style=discord.ButtonStyle.primary,
            disabled=not is_ready,
            custom_id="clean_ticket_submit",
            row=1
        )
        btn_submit.callback = self.submit_callback
        self.add_item(btn_submit)

        # ปุ่ม 3: แดง (Danger) - ใช้สีแดงเฉพาะปุ่มสำคัญที่ลบข้อมูล/ปิดห้อง
        btn_close = Button(
            label="ปิดตั๋วนี้",
            style=discord.ButtonStyle.danger,
            custom_id="clean_ticket_close",
            row=2
        )
        btn_close.callback = self.close_callback
        self.add_item(btn_close)

    async def select_role_callback(self, interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        await interaction.response.send_message(
            f"**เลือกแผนกเรียบร้อย**\n"
            f"-# แผนก: `{selected.upper()}` กรุณากดปุ่ม **กรอกข้อมูลตัวละคร** เพื่อดำเนินการต่อ",
            ephemeral=True
        )

    async def fill_info_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CleanCombinedInfoModal())

    async def submit_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "**ส่งข้อมูลสำเร็จ**\n-# ระบบได้ส่งข้อมูลให้แอดมินตรวจสอบเรียบร้อยแล้วครับ",
            ephemeral=True
        )

    async def close_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "-# ตั๋วนี้กำลังจะถูกปิดและลบห้องในอีก 5 วินาที...",
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
                thumbnail_url=config.get("EMBED_THUMBNAIL_URL"),
                extra_fields=extra_fields
            )

            await target_ch.send(
                content="### ─── 01. ระบบลงเวลา (TIME TRACKER) ───",
                embeds=op_embeds,
                view=CleanOperationView()
            )

        # ----------------------------------------------------------------------
        # 2. Welcome & Ticket Preview (Clean Minimalist — ศัพท์ทั่วไป)
        # ----------------------------------------------------------------------
        if component in ["all", "welcome"]:
            welcome_panel_embed = discord.Embed(
                title="WHAT UNIVERSAL — REGISTRATION",
                description=(
                    "-# ระบบลงทะเบียนและยืนยันตัวตนสมาชิกใหม่\n\n"
                    "ยินดีต้อนรับสู่ What Universal\n"
                    "กดปุ่มด้านล่างเพื่อเปิดตั๋วลงทะเบียนและกรอกข้อมูล"
                ),
                color=c_slate
            )
            welcome_panel_embed.set_footer(text="ระบบลงทะเบียนอัตโนมัติ 24 ชม.")
            if config.get("EMBED_THUMBNAIL_URL"):
                welcome_panel_embed.set_thumbnail(url=config.get("EMBED_THUMBNAIL_URL"))

            btn_open_ticket = Button(
                label="เปิดตั๋วลงทะเบียน",
                style=discord.ButtonStyle.primary
            )
            v_welcome = View()
            v_welcome.add_item(btn_open_ticket)

            await target_ch.send(
                content="### ─── 02.1 แผงกดเปิดตั๋วลงทะเบียน (WELCOME PANEL) ───",
                embed=welcome_panel_embed,
                view=v_welcome
            )

            # Ticket Screen Checklist (ภายในตั๋ว)
            ticket_active_embed = discord.Embed(
                title="ตั๋วลงทะเบียน #0042",
                description="-# กรุณากรอกข้อมูลให้ครบเพื่อรับยศในเซิร์ฟเวอร์",
                color=c_ice
            )
            ticket_active_embed.add_field(
                name="1. แผนก / ตำแหน่ง",
                value="`ตำรวจ (Police)` (เลือกแล้ว)",
                inline=False
            )
            ticket_active_embed.add_field(
                name="2. ข้อมูลตัวละคร (IC)",
                value="`Somchai Jaidee` • เบอร์: `081-234-5678`",
                inline=False
            )
            ticket_active_embed.add_field(
                name="3. รูปถ่าย / สตอรี่",
                value="`รอส่งรูป` (แนบรูปส่งในแชทนี้ได้เลย)",
                inline=False
            )
            ticket_active_embed.set_footer(text="เมื่อข้อมูลครบแล้ว ปุ่มยืนยันจะเปิดให้กดอัตโนมัติ")

            await target_ch.send(
                content="### ─── 02.2 หน้าจอภายในตั๋ว (INSIDE TICKET) ───",
                embed=ticket_active_embed,
                view=CleanTicketInteractiveView(step=2)
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
                thumbnail_url=config.get("EMBED_THUMBNAIL_URL"),
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
