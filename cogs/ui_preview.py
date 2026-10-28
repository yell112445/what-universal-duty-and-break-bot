import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Button, Select
from datetime import datetime, timedelta
from config_manager import config

# ==============================================================================
# 1. Preview Components: Operations System (ระบบปฏิบัติการใหม่)
# ==============================================================================

class PreviewOTPModal(Modal):
    def __init__(self):
        super().__init__(title="🔐 ยืนยันรหัส OTP เข้าเวร")
        otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
        self.otp_input = TextInput(
            label="รหัส OTP ประจำรอบ",
            placeholder=f"ดูรหัส OTP ได้ที่ห้อง #{otp_ch_id} (เช่น 4821)",
            required=True,
            min_length=4,
            max_length=6
        )
        self.add_item(self.otp_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"✅ [UI Preview] ยืนยันรหัส OTP `{self.otp_input.value.strip()}` สำเร็จ!\n"
            f"⏱️ ระบบเริ่มบันทึกเวลาเข้าเวรของคุณเรียบร้อยแล้ว",
            ephemeral=True
        )

class PreviewOperationView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="เข้าเวรปฏิบัติการ",
        style=discord.ButtonStyle.success,
        emoji="🟢",
        custom_id="preview_op_checkin"
    )
    async def checkin_button(self, interaction: discord.Interaction, button: Button):
        # จำลองเปิด Modal ขอ OTP
        await interaction.response.send_modal(PreviewOTPModal())

    @discord.ui.button(
        label="ออกเวร / สิ้นสุด",
        style=discord.ButtonStyle.danger,
        emoji="🔴",
        custom_id="preview_op_checkout"
    )
    async def checkout_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="🔴 ยืนยันการออกเวรปฏิบัติการ",
            description=(
                f"**เจ้าหน้าที่:** {interaction.user.mention}\n"
                f"**เวลาเข้าเวร:** `10:00:00`\n"
                f"**เวลาออกเวร:** `{datetime.now(config.timezone).strftime('%H:%M:%S')}`\n"
                f"**รวมระยะเวลาปฏิบัติงาน:** `2 ชั่วโมง 15 นาที`\n\n"
                f"ระบบได้บันทึกประวัติการปฏิบัติหน้าที่ของคุณเรียบร้อยแล้ว ขอบคุณสำหรับการปฏิบัติงานครับ 🫡"
            ),
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="เช็คสถานะของฉัน",
        style=discord.ButtonStyle.secondary,
        emoji="📋",
        custom_id="preview_op_status"
    )
    async def status_button(self, interaction: discord.Interaction, button: Button):
        now_ts = int(datetime.now().timestamp())
        embed = discord.Embed(
            title="📋 สถานะการปฏิบัติงานปัจจุบัน",
            color=discord.Color.blue()
        )
        embed.add_field(name="สถานะ", value="🟢 กำลังปฏิบัติหน้าที่", inline=True)
        embed.add_field(name="เริ่มปฏิบัติหน้าที่เมื่อ", value=f"<t:{now_ts - 3600}:R>", inline=True)
        embed.add_field(name="เวลารวมวันนี้", value="`3 ชั่วโมง 40 นาที`", inline=False)
        embed.set_footer(text="ข้อมูลอัปเดตแบบเรียลไทม์")
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# 2. Preview Components: Welcome & Introduction System (ระบบต้อนรับ & ตั๋วใหม่)
# ==============================================================================

class PreviewCombinedInfoModal(Modal):
    def __init__(self):
        super().__init__(title="📝 กรอกข้อมูลตัวละคร (IC)")
        self.name_input = TextInput(
            label="ชื่อ - นามสกุล ตัวละคร (IC)",
            placeholder="เช่น Somchai Jaidee",
            required=True,
            max_length=60
        )
        self.phone_input = TextInput(
            label="เบอร์โทรศัพท์ในเมือง (IC)",
            placeholder="เช่น 081-234-5678 หรือ 123456",
            required=True,
            max_length=20
        )
        self.add_item(self.name_input)
        self.add_item(self.phone_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"✅ [UI Preview] บันทึกข้อมูลสำเร็จ!\n"
            f"• ชื่อ: **{self.name_input.value.strip()}**\n"
            f"• เบอร์โทร: **{self.phone_input.value.strip()}**\n\n"
            f"📌 กรุณาแนบรูปภาพฟอร์ม/สตอรี่ลงในห้องนี้เพื่อปลดล็อคปุ่มยืนยัน",
            ephemeral=True
        )

class PreviewTicketInteractiveView(View):
    def __init__(self, step: int = 1):
        super().__init__(timeout=None)
        self.step = step

        # Role select
        options = [
            discord.SelectOption(label="หน่วยตำรวจ (Police)", value="police", emoji="👮"),
            discord.SelectOption(label="นักเรียนตำรวจฝึกหัด (Beginner)", value="beginner", emoji="🔰"),
            discord.SelectOption(label="หน่วยแพทย์ (MD)", value="md", emoji="🚑"),
            discord.SelectOption(label="ผู้ช่วย / ผู้ประสานงาน (Assistant)", value="assistant", emoji="👔")
        ]
        select = Select(
            placeholder="1️⃣ เลือกสายงานที่สังกัด...",
            options=options,
            custom_id="preview_ticket_role_select",
            row=0
        )
        select.callback = self.select_role_callback
        self.add_item(select)

        # Button 1: กรอกข้อมูล (ชื่อ + เบอร์ในฟอร์มเดียว)
        btn_info = Button(
            label="กรอกข้อมูลตัวละคร (ชื่อ + เบอร์)",
            style=discord.ButtonStyle.primary,
            emoji="📝",
            custom_id="preview_ticket_fill_info",
            row=1
        )
        btn_info.callback = self.fill_info_callback
        self.add_item(btn_info)

        # Button 2: ปุ่มยืนยัน (ตัวอย่างการเป็น Disabled state เมื่อยังกรอกไม่ครบ)
        is_ready = (step >= 3)
        btn_submit = Button(
            label="ยืนยันและส่งเรื่องเสร็จสิ้น" if is_ready else "กรุณากรอกข้อมูลให้ครบก่อนส่ง",
            style=discord.ButtonStyle.success if is_ready else discord.ButtonStyle.secondary,
            emoji="✅" if is_ready else "⏳",
            disabled=not is_ready,
            custom_id="preview_ticket_submit",
            row=1
        )
        btn_submit.callback = self.submit_callback
        self.add_item(btn_submit)

        # Button 3: ปิดตั๋ว
        btn_close = Button(
            label="ปิดตั๋วนี้",
            style=discord.ButtonStyle.danger,
            emoji="🔒",
            custom_id="preview_ticket_close",
            row=2
        )
        btn_close.callback = self.close_callback
        self.add_item(btn_close)

    async def select_role_callback(self, interaction: discord.Interaction):
        selected = interaction.data["values"][0]
        await interaction.response.send_message(
            f"✅ [UI Preview] เลือกสายงาน `{selected.upper()}` สำเร็จ! ขั้นต่อไปกรุณากดปุ่ม **กรอกข้อมูลตัวละคร**",
            ephemeral=True
        )

    async def fill_info_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PreviewCombinedInfoModal())

    async def submit_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🎉 [UI Preview] ส่งข้อมูลครบถ้วนแล้ว! แอดมินและระบบจะดำเนินการตรวจสอบและมอบยศให้โดยอัตโนมัติ",
            ephemeral=True
        )

    async def close_callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🔒 [UI Preview] ตั๋วนี้กำลังจะถูกปิดและลบห้องในอีก 5 วินาที...",
            ephemeral=True
        )


# ==============================================================================
# 3. Preview Components: Breaktime System (ระบบลางานใหม่)
# ==============================================================================

class PreviewLeavePanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="ยื่นคำขอลางาน 7 วัน (L-7)",
        style=discord.ButtonStyle.primary,
        emoji="🌴",
        custom_id="preview_leave_l7"
    )
    async def l7_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "📝 [UI Preview] เปิดหน้าต่างระบุวันที่เริ่มลา 7 วัน (ระบบจะคำนวณวันสิ้นสุดและตรวจสอบโควตาให้อัตโนมัติ)",
            ephemeral=True
        )

    @discord.ui.button(
        label="ยื่นคำขอลางาน 3-4 วัน (L-3/4)",
        style=discord.ButtonStyle.secondary,
        emoji="⛱️",
        custom_id="preview_leave_l34"
    )
    async def l34_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "📝 [UI Preview] เปิดหน้าต่างระบุวันที่เริ่มลา 3-4 วัน (ระบบจะคำนวณวันสิ้นสุดและตรวจสอบโควตาให้อัตโนมัติ)",
            ephemeral=True
        )

    @discord.ui.button(
        label="เช็คโควตาวันลาของฉัน",
        style=discord.ButtonStyle.success,
        emoji="🔍",
        custom_id="preview_leave_check"
    )
    async def check_button(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="📊 สิทธิ์โควตาวันลาของคุณ",
            description=f"**เจ้าหน้าที่:** {interaction.user.mention}\n**สถานะ:** ปกติ (สามารถยื่นลาได้)",
            color=discord.Color.green()
        )
        embed.add_field(name="โควตาลา 7 วัน", value="เหลือสิทธิ์ `1 ครั้ง/เดือน`", inline=True)
        embed.add_field(name="โควตาลา 3-4 วัน", value="เหลือสิทธิ์ `2 ครั้ง/เดือน`", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==============================================================================
# Cog หลัก: UI Preview Cog
# ==============================================================================

class UIPreviewCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _check_admin(self, interaction: discord.Interaction) -> bool:
        return config.is_owner(interaction.user.id)

    def _get_preview_channel(self) -> discord.TextChannel | None:
        ch_id = config.get("UI_PREVIEW_CHANNEL_ID")
        if not ch_id:
            return None
        return self.bot.get_channel(int(ch_id))

    @app_commands.command(
        name="preview_ui",
        description="[Admin] ส่งตัวอย่างหน้าตา UX/UI ที่ออกแบบใหม่ไปยังห้อง Preview เพื่อทดสอบ"
    )
    @app_commands.describe(
        component="ระบบที่ต้องการดูตัวอย่าง (เลือก 'all' เพื่อดูทั้งหมด)"
    )
    @app_commands.choices(component=[
        app_commands.Choice(name="ทั้งหมด (All Systems)", value="all"),
        app_commands.Choice(name="1. ระบบปฏิบัติการ (Operations Panel & Board)", value="operation"),
        app_commands.Choice(name="2. ระบบต้อนรับ & ตั๋วใหม่ (Welcome Ticket)", value="welcome"),
        app_commands.Choice(name="3. ระบบลางาน & บอร์ดสด (Breaktime & Board)", value="leave")
    ])
    async def preview_ui_command(self, interaction: discord.Interaction, component: str = "all"):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        target_ch = self._get_preview_channel()
        if not target_ch:
            return await interaction.response.send_message(
                f"❌ ไม่พบห้อง Preview (Channel ID: `{config.get('UI_PREVIEW_CHANNEL_ID')}`) กรุณาตรวจการตั้งค่าใน config",
                ephemeral=True
            )

        await interaction.response.defer(ephemeral=True)

        now_ts = int(datetime.now().timestamp())
        auto_stop_ts = int((datetime.now() + timedelta(minutes=45)).timestamp())

        # ----------------------------------------------------------------------
        # 1. Operation Preview
        # ----------------------------------------------------------------------
        if component in ["all", "operation"]:
            op_embed = discord.Embed(
                title="🛡️ ศูนย์บันทึกเวลาปฏิบัติการ — WHAT UNIVERSAL",
                description=(
                    "ยินดีต้อนรับเจ้าหน้าที่ทุกนาย กรุณากดปุ่มด้านล่างเพื่อทำการ **เข้าเวร** หรือ **ออกเวร**\n"
                    "ระบบจะบันทึกเวลาและคำนวณชั่วโมงการปฏิบัติงานโดยอัตโนมัติ"
                ),
                color=discord.Color.from_rgb(46, 204, 113)
            )
            op_embed.add_field(
                name="🟢 สถานะรอบปัจจุบัน",
                value="`กำลังเปิดรอบปฏิบัติการ (Active)`",
                inline=True
            )
            op_embed.add_field(
                name="⏰ รอบปิดเวรอัตโนมัติถัดไป",
                value=f"<t:{auto_stop_ts}:t> (<t:{auto_stop_ts}:R>)",
                inline=True
            )
            op_embed.add_field(
                name="🔐 รหัส OTP ประจำรอบ",
                value=f"ดูรหัสยืนยัน 4 หลักได้ที่ห้อง <#{config.get('OPERATION_OTP_CHANNEL')}>",
                inline=False
            )
            op_embed.add_field(
                name="👥 สมาชิกที่กำลังปฏิบัติหน้าที่ (3 นาย)",
                value=(
                    f"• 👮 `[SWAT]` **Jackson Teller** • เข้าเวรเมื่อ <t:{now_ts - 3600}:R>\n"
                    f"• 👮 `[POL]` **Sarah Connor** • เข้าเวรเมื่อ <t:{now_ts - 1800}:R>\n"
                    f"• 👮 `[AIR]` **Maverick Mitchell** • เข้าเวรเมื่อ <t:{now_ts - 600}:R>"
                ),
                inline=False
            )
            op_embed.set_footer(text="What Universal Police • Modern UX/UI Concept")
            if config.get("EMBED_THUMBNAIL_URL"):
                op_embed.set_thumbnail(url=config.get("EMBED_THUMBNAIL_URL"))

            await target_ch.send(
                content="### 📌 [UX/UI Preview] 1. ระบบบันทึกเวลาปฏิบัติการ (Operations Redesign)",
                embed=op_embed,
                view=PreviewOperationView()
            )

        # ----------------------------------------------------------------------
        # 2. Welcome & Ticket Preview
        # ----------------------------------------------------------------------
        if component in ["all", "welcome"]:
            # 2.1 หน้าตั๋วหลัก
            welcome_panel_embed = discord.Embed(
                title="🎫 ศูนย์ลงทะเบียน & ทำบัตรประจำตัว — WHAT UNIVERSAL",
                description=(
                    "ยินดีต้อนรับสมาชิกใหม่ทุกท่านเข้าสู่สังกัด What Universal\n\n"
                    "**ขั้นตอนการทำเรื่อง:**\n"
                    "1. กดปุ่ม **เปิดตั๋วลงทะเบียน** ด้านล่าง\n"
                    "2. เลือกสายงานที่สังกัด (ตำรวจ, หน่วยฝึก, แพทย์, ผู้ช่วย)\n"
                    "3. กรอกข้อมูลตัวละคร (ชื่อ IC + เบอร์โทร) ในหน้าต่างเดียว\n"
                    "4. แนบรูปภาพสตอรี่หรือฟอร์มประจำตัวในห้องตั๋ว\n"
                    "5. กดยืนยันเพื่อรับยศและเริ่มปฏิบัติงาน"
                ),
                color=discord.Color.from_rgb(52, 152, 219)
            )
            welcome_panel_embed.set_footer(text="ระบบตั๋วอัตโนมัติ 24 ชม.")
            if config.get("EMBED_THUMBNAIL_URL"):
                welcome_panel_embed.set_thumbnail(url=config.get("EMBED_THUMBNAIL_URL"))

            btn_open_ticket = Button(
                label="เปิดตั๋วลงทะเบียนใหม่ (Start Ticket)",
                style=discord.ButtonStyle.primary,
                emoji="🎫"
            )
            v_welcome = View()
            v_welcome.add_item(btn_open_ticket)

            await target_ch.send(
                content="### 📌 [UX/UI Preview] 2.1 แผงกดเปิดตั๋วรับผู้เล่นใหม่ (Welcome Panel)",
                embed=welcome_panel_embed,
                view=v_welcome
            )

            # 2.2 หน้าจอภายในห้องตั๋วของผู้เล่น (Active Ticket Checklist)
            ticket_active_embed = discord.Embed(
                title="📝 ตั๋วลงทะเบียนประจำตัว: สมชาย ใจดี",
                description=(
                    "กรุณาดำเนินการตามรายการด้านล่างให้ครบถ้วนทุกข้อ\n"
                    "เมื่อข้อมูลครบถ้วนแล้ว **ปุ่มยืนยันจะเปิดให้กดโดยอัตโนมัติ**"
                ),
                color=discord.Color.orange()
            )
            ticket_active_embed.add_field(
                name="1️⃣ สายงานที่สังกัด",
                value="✅ `ตำรวจนครบาล (Police)`",
                inline=False
            )
            ticket_active_embed.add_field(
                name="2️⃣ ข้อมูลตัวละคร (IC)",
                value="✅ **ชื่อ-นามสกุล:** `Somchai Jaidee`\n✅ **เบอร์โทรศัพท์:** `081-234-5678`",
                inline=False
            )
            ticket_active_embed.add_field(
                name="3️⃣ รูปภาพสตอรี่ / ฟอร์ม",
                value="❌ `ยังไม่พบรูปภาพ` (กรุณาแนบรูปภาพแล้วส่งลงในแชทนี้)",
                inline=False
            )
            ticket_active_embed.set_footer(text="ตัวอย่างการแสดงผล Checklist แบบ Realtime ภายในตั๋ว")

            await target_ch.send(
                content="### 📌 [UX/UI Preview] 2.2 หน้าจอภายในตั๋ว (Dynamic Checklist & Single Modal)",
                embed=ticket_active_embed,
                view=PreviewTicketInteractiveView(step=2)
            )

        # ----------------------------------------------------------------------
        # 3. Leave Board & Panel Preview
        # ----------------------------------------------------------------------
        if component in ["all", "leave"]:
            leave_board_embed = discord.Embed(
                title="🌴 กระดานสถานะการลางาน — WHAT UNIVERSAL",
                description="อัปเดตสถานะเจ้าหน้าที่ที่ขออนุมัติลางานแบบเรียลไทม์",
                color=discord.Color.teal()
            )
            leave_board_embed.add_field(
                name="🔴 กำลังอยู่ในช่วงลางาน (Active Leaves - 2 นาย)",
                value=(
                    f"• 👮 `[SWAT]` **Alex Mercer** • ลา 7 วัน\n"
                    f"  └ สิ้นสุด: <t:{now_ts + 86400 * 3}:D> (เหลืออีก 3 วัน)\n"
                    f"• 👮 `[POL]` **Diana Prince** • ลา 3 วัน\n"
                    f"  └ สิ้นสุด: <t:{now_ts + 86400}:D> (เหลืออีก 1 วัน)"
                ),
                inline=False
            )
            leave_board_embed.add_field(
                name="🟡 แจ้งลางานล่วงหน้า (Upcoming Leaves - 1 นาย)",
                value=(
                    f"• 👮 `[AIR]` **Thomas Wayne** • ลา 4 วัน\n"
                    f"  └ เริ่ม: <t:{now_ts + 86400 * 2}:D> ถึง <t:{now_ts + 86400 * 6}:D>"
                ),
                inline=False
            )
            leave_board_embed.add_field(
                name="📊 สรุปโควตาคงเหลือวันนี้",
                value="อนุมัติแล้ว `2 / 5` คน (ยังว่างอีก `3` ที่นั่ง)",
                inline=False
            )
            leave_board_embed.set_footer(text="Leave Board อัปเดตล่าสุด")

            leave_panel_embed = discord.Embed(
                title="📋 แผงยื่นคำขอลางานประจำการ",
                description=(
                    "เจ้าหน้าที่สามารถยื่นความประสงค์ขอลาปฏิบัติหน้าที่ได้ที่นี่\n"
                    "• **ลา 7 วัน (L-7):** เหมาะสำหรับการลาพักผ่อนประจำเดือน\n"
                    "• **ลา 3-4 วัน (L-3/4):** เหมาะสำหรับภารกิจสั้นเร่งด่วน\n\n"
                    "⚠️ กรุณาตรวจสอบโควตาคงเหลือก่อนกดยื่นคำขอ"
                ),
                color=discord.Color.from_rgb(41, 128, 185)
            )

            await target_ch.send(
                content="### 📌 [UX/UI Preview] 3. ระบบลางาน & กระดานสด (Breaktime & Leave Board)",
                embed=leave_board_embed
            )
            await target_ch.send(
                embed=leave_panel_embed,
                view=PreviewLeavePanelView()
            )

        await interaction.followup.send(
            f"✅ ส่งตัวอย่าง UX/UI Component ไปยังห้อง <#{target_ch.id}> เรียบร้อยแล้ว!\n"
            f"คุณสามารถเปิดดูและคลิกทดสอบปุ่ม/Modal ได้สดๆ ในดิสคอร์ดทันที",
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
