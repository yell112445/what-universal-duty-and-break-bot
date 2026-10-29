import os
import sys
import asyncio
import json
import logging
import subprocess
from datetime import datetime
from typing import Optional, Any
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import Modal, TextInput, View, Select
from config_manager import config

logger = logging.getLogger("ConfigCog")

class ConfigEditModal(Modal):
    def __init__(self, key: str, current_value: str, display_name: str, value_type: str = "str"):
        super().__init__(title=f"แก้ไข {display_name[:40]}")
        self.key = key
        self.value_type = value_type
        self.val_input = TextInput(
            label="ค่าใหม่",
            default=str(current_value),
            required=True,
            max_length=1000
        )
        self.add_item(self.val_input)

    async def on_submit(self, interaction: discord.Interaction):
        raw_val = self.val_input.value.strip()
        parsed_val = raw_val

        try:
            if self.value_type == "int":
                parsed_val = int(raw_val)
            elif self.value_type == "bool":
                parsed_val = raw_val.lower() in ["true", "1", "yes", "เปิด", "on"]
            elif self.value_type == "list_str":
                if self.key == "AUTO_STOP_TIMES":
                    import re
                    matches = re.findall(r"\b(\d{1,2}):(\d{2})\b", raw_val)
                    parsed_val = [f"{int(h):02d}:{int(m):02d}" for h, m in matches]
                else:
                    parsed_val = [x.strip() for x in raw_val.replace(",", " ").replace("/", " ").split() if x.strip()]
            elif self.value_type == "list_int":
                parsed_val = [int(x.strip()) for x in raw_val.replace(",", " ").split() if x.strip()]
            elif self.value_type == "json":
                parsed_val = json.loads(raw_val)
        except Exception as e:
            return await interaction.response.send_message(
                f"❌ รูปแบบข้อมูลไม่ถูกต้อง ({self.value_type}): {e}",
                ephemeral=True
            )

        success = await config.set(self.key, parsed_val)
        if success:
            await interaction.response.send_message(
                f"✅ อัปเดต `{self.key}` เป็น `{parsed_val}` สำเร็จ!\n(มีผลทันทีโดยไม่ต้องรีสตาร์ตบอท)",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ บันทึกค่าล้มเหลว กรุณาตรวจสอบ log",
                ephemeral=True
            )

class ConfigSelectView(View):
    def __init__(self, current_configs: dict):
        super().__init__(timeout=180)
        self.current_configs = current_configs

        options = [
            discord.SelectOption(
                label="เวลาปิดปฏิบัติการอัตโนมัติ",
                value="AUTO_STOP_TIMES",
                description="เช่น 03:00, 18:00, 20:32"
            ),
            discord.SelectOption(
                label="สลับ เปิด/ปิด Duty Tracker",
                value="DUTY_TRACKER_ENABLED",
                description="true หรือ false"
            ),
            discord.SelectOption(
                label="ช่องส่ง Log ปฏิบัติการ",
                value="OPERATION_LOG_CHANNEL_ID",
                description="Channel ID ของห้อง Log"
            ),
            discord.SelectOption(
                label="ช่องแสดง OTP ปฏิบัติการ",
                value="OPERATION_OTP_CHANNEL",
                description="Channel ID ของห้อง OTP"
            ),
            discord.SelectOption(
                label="ช่องแจ้งเตือน Assistant",
                value="OPERATION_LOG_ASSISTANT_CHANNEL_ID",
                description="Channel ID ของห้อง Assistant Log"
            ),
            discord.SelectOption(
                label="ช่อง Leave Board",
                value="LEAVE_BOARD_CHANNEL_ID",
                description="Channel ID สำหรับบอร์ดลางาน"
            ),
            discord.SelectOption(
                label="ช่อง Leave Panel",
                value="LEAVE_PANEL_CHANNEL_ID",
                description="Channel ID สำหรับแผงปุ่มกดลางาน"
            ),
            discord.SelectOption(
                label="ช่องแจ้งเตือนออกเวรนาน (Alert)",
                value="ALERT_CHANNEL_ID",
                description="Channel ID ห้องแจ้งเตือนเวลาหลุดเวร"
            ),
            discord.SelectOption(
                label="อายุรหัส OTP (นาที)",
                value="OTP_EXPIRY_MINUTES",
                description="จำนวนนาทีก่อน OTP หมดอายุ"
            ),
            discord.SelectOption(
                label="เวลาสูงสุดที่อนุญาตให้ออกเวร (วินาที)",
                value="MAX_OFF_DUTY_GAP_SECONDS",
                description="ค่าเริ่มต้น 3600 (1 ชั่วโมง)"
            ),
            discord.SelectOption(
                label="สลับ เปิด/ปิด ลา 7 วัน",
                value="FEATURE_L_7_ENABLED",
                description="true หรือ false"
            ),
            discord.SelectOption(
                label="สลับ เปิด/ปิด ลา 3/4 วัน",
                value="FEATURE_L_3_4_ENABLED",
                description="true หรือ false"
            ),
            discord.SelectOption(
                label="สลับ เปิด/ปิด Welcome Ticket",
                value="WELCOME_TICKET_ENABLED",
                description="true หรือ false"
            ),
            discord.SelectOption(
                label="ช่อง Welcome Ticket Panel",
                value="WELCOME_TICKET_CHANNEL_ID",
                description="Channel ID ของห้องกดรับ Ticket"
            ),
            discord.SelectOption(
                label="ช่อง Join Log สมาชิกเข้าเซิร์ฟ",
                value="WELCOME_JOIN_LOG_CHANNEL_ID",
                description="Channel ID ห้อง Log คนเข้า"
            ),
            discord.SelectOption(
                label="ช่อง Data Log สรุปข้อมูลผู้เล่น",
                value="WELCOME_DATA_LOG_CHANNEL_ID",
                description="Channel ID ห้อง Log Ticket ที่เสร็จสิ้น"
            ),
            discord.SelectOption(
                label="ช่อง Preview UX/UI สำหรับทดสอบ",
                value="UI_PREVIEW_CHANNEL_ID",
                description="Channel ID ห้องดูตัวอย่าง UI"
            )
        ]

        # Safeguard: Chunk options to max 25 per dropdown row to prevent Discord API limit errors
        for idx, chunk in enumerate([options[i:i + 25] for i in range(0, len(options), 25)]):
            placeholder = "เลือกค่าที่ต้องการแก้ไข..." if idx == 0 else f"เลือกค่าที่ต้องการแก้ไข... (ชุดที่ {idx + 1})"
            select = Select(placeholder=placeholder, options=chunk, row=idx)
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        key = interaction.data["values"][0]
        curr_val = config.get(key, "")

        val_types = {
            "AUTO_STOP_TIMES": "list_str",
            "DUTY_TRACKER_ENABLED": "bool",
            "OPERATION_LOG_CHANNEL_ID": "int",
            "OPERATION_OTP_CHANNEL": "int",
            "OPERATION_LOG_ASSISTANT_CHANNEL_ID": "int",
            "LEAVE_BOARD_CHANNEL_ID": "int",
            "LEAVE_PANEL_CHANNEL_ID": "int",
            "ALERT_CHANNEL_ID": "int",
            "OTP_EXPIRY_MINUTES": "int",
            "MAX_OFF_DUTY_GAP_SECONDS": "int",
            "FEATURE_L_7_ENABLED": "bool",
            "FEATURE_L_3_4_ENABLED": "bool",
            "WELCOME_TICKET_ENABLED": "bool",
            "WELCOME_TICKET_CHANNEL_ID": "int",
            "WELCOME_TICKET_CATEGORY_ID": "int",
            "WELCOME_DATA_LOG_CHANNEL_ID": "int",
            "WELCOME_JOIN_LOG_CHANNEL_ID": "int",
            "LINK_LOG_CHANNEL_ID": "int",
            "UI_PREVIEW_CHANNEL_ID": "int"
        }
        val_type = val_types.get(key, "str")
        modal = ConfigEditModal(key, str(curr_val), key, val_type)
        await interaction.response.send_modal(modal)

class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _check_admin(self, interaction: discord.Interaction) -> bool:
        """อนุญาตเฉพาะเจ้าของบอท (Owner ID: 707212479490424833) เพียงคนเดียวเท่านั้น"""
        return config.is_owner(interaction.user.id)

    async def _send_config_panel(self, interaction: discord.Interaction):
        """ส่งแผงควบคุมการตั้งค่าภาพรวม"""
        embed = discord.Embed(
            title="⚙️ ศูนย์กลางการตั้งค่า (What Universal Bot)",
            description="ค่าทั้งหมดจะถูกบันทึกลง `data/config.json` และมีผลทันทีโดยไม่ต้องรีสตาร์ตบอท",
            color=discord.Color.blurple()
        )

        # หมวดหมู่ 1: Server & Flags
        embed.add_field(
            name="🏢 Server & Feature Flags",
            value=(
                f"• Guild ID: `{config.guild_id}`\n"
                f"• Duty Tracker: `{'เปิดใช้งาน ✅' if config.get('DUTY_TRACKER_ENABLED') else 'ปิดใช้งานชั่วคราว ⏸️'}`\n"
                f"• ลา 7 วัน: `{'เปิด' if config.get('FEATURE_L_7_ENABLED', True) else 'ปิด'}` | ลา 3/4 วัน: `{'เปิด' if config.get('FEATURE_L_3_4_ENABLED', True) else 'ปิด'}`"
            ),
            inline=False
        )

        # หมวดหมู่ 2: Operations
        embed.add_field(
            name="🛡️ Operations",
            value=(
                f"• Log Channel: <#{config.get('OPERATION_LOG_CHANNEL_ID')}>\n"
                f"• OTP Channel: <#{config.get('OPERATION_OTP_CHANNEL')}>\n"
                f"• Assistant Log: <#{config.get('OPERATION_LOG_ASSISTANT_CHANNEL_ID')}>\n"
                f"• Personal Log: <#{config.get('OPERATION_PERSONAL_LOG_CHANNELS')}>\n"
                f"• อายุ OTP: `{config.get('OTP_EXPIRY_MINUTES')} นาที`\n"
                f"• เวลาหยุดอัตโนมัติ: `{', '.join(config.get('AUTO_STOP_TIMES', []))}`"
            ),
            inline=False
        )

        # หมวดหมู่ 3: Breaktime & Duty Check
        embed.add_field(
            name="🌴 Breaktime & Duty",
            value=(
                f"• Board Channel: <#{config.get('LEAVE_BOARD_CHANNEL_ID')}>\n"
                f"• Panel Channel: <#{config.get('LEAVE_PANEL_CHANNEL_ID')}>\n"
                f"• Alert Channel: <#{config.get('ALERT_CHANNEL_ID')}>\n"
                f"• Max Off-duty Gap: `{config.get('MAX_OFF_DUTY_GAP_SECONDS', 3600)} วินาที`"
            ),
            inline=False
        )

        # หมวดหมู่ 4: Welcome & Introduction
        embed.add_field(
            name="🎫 Welcome & Introduction",
            value=(
                f"• Welcome Ticket: `{'เปิดใช้งาน ✅' if config.get('WELCOME_TICKET_ENABLED', True) else 'ปิดใช้งานชั่วคราว ⏸️'}`\n"
                f"• Ticket Panel: <#{config.get('WELCOME_TICKET_CHANNEL_ID')}>\n"
                f"• Category ID: `{config.get('WELCOME_TICKET_CATEGORY_ID')}`\n"
                f"• Data Log: <#{config.get('WELCOME_DATA_LOG_CHANNEL_ID')}>\n"
                f"• Join Log: <#{config.get('WELCOME_JOIN_LOG_CHANNEL_ID')}>\n"
                f"• Link Log: <#{config.get('LINK_LOG_CHANNEL_ID')}>\n"
                f"• UI Preview: <#{config.get('UI_PREVIEW_CHANNEL_ID')}>"
            ),
            inline=False
        )

        view = ConfigSelectView(config._config)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _handle_set_config(self, interaction: discord.Interaction, key: Optional[str] = None, value: Optional[str] = None):
        """ประมวลผลคำสั่ง set_config / config_set"""
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        # 1. ถ้าไม่ใส่ key -> แสดงแผงตั้งค่าภาพรวม
        if not key:
            return await self._send_config_panel(interaction)

        key = key.strip()

        # 2. ถ้าใส่ key แต่เว้นว่าง value -> เปิด Modal ให้กรอก
        if value is None:
            current = config.get(key)
            val_type = "str"
            if isinstance(current, bool):
                val_type = "bool"
            elif isinstance(current, int):
                val_type = "int"
            elif isinstance(current, list):
                val_type = "list_str"
            modal = ConfigEditModal(key, str(current if current is not None else ""), key, val_type)
            return await interaction.response.send_modal(modal)

        # 3. ถ้าใส่ทั้ง key และ value -> บันทึกค่าทันที
        await interaction.response.defer(ephemeral=True)
        raw_val = value.strip()
        parsed_val: Any = raw_val

        current = config.get(key)
        if isinstance(current, bool):
            parsed_val = raw_val.lower() in ["true", "1", "yes", "เปิด", "on"]
        elif isinstance(current, int) or (isinstance(raw_val, str) and raw_val.isdigit()):
            try:
                parsed_val = int(raw_val)
            except ValueError:
                pass
        elif isinstance(current, list):
            if raw_val.startswith("[") and raw_val.endswith("]"):
                try:
                    parsed_val = json.loads(raw_val)
                except Exception:
                    parsed_val = [x.strip() for x in raw_val.replace(",", " ").split() if x.strip()]
            else:
                parsed_val = [x.strip() for x in raw_val.replace(",", " ").split() if x.strip()]

        success = await config.set(key, parsed_val)
        if success:
            await interaction.followup.send(
                f"✅ อัปเดต `{key}` เป็น `{parsed_val}` สำเร็จเรียบร้อย!\n(มีผลทันทีโดยไม่ต้องรีสตาร์ตบอท)",
                ephemeral=True
            )
        else:
            await interaction.followup.send("❌ บันทึกค่าล้มเหลว กรุณาตรวจสอบ log", ephemeral=True)

    async def _key_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        known_keys = [
            "OPERATION_OTP_CHANNEL",
            "OPERATION_LOG_CHANNEL_ID",
            "OPERATION_LOG_ASSISTANT_CHANNEL_ID",
            "OPERATION_PERSONAL_LOG_CHANNELS",
            "AUTO_STOP_TIMES",
            "OTP_EXPIRY_MINUTES",
            "BUTTON_COOLDOWN_SECONDS",
            "AUTO_STOP_ANNOUNCE_MINUTES",
            "DUTY_TRACKER_ENABLED",
            "LEAVE_BOARD_CHANNEL_ID",
            "LEAVE_PANEL_CHANNEL_ID",
            "FEATURE_L_7_ENABLED",
            "FEATURE_L_3_4_ENABLED",
            "WELCOME_TICKET_ENABLED",
            "WELCOME_TICKET_CHANNEL_ID",
            "WELCOME_TICKET_CATEGORY_ID",
            "WELCOME_DATA_LOG_CHANNEL_ID",
            "WELCOME_JOIN_LOG_CHANNEL_ID",
            "LINK_LOG_CHANNEL_ID",
            "UI_PREVIEW_CHANNEL_ID",
            "POLICE_ROLE_ID",
            "BEGINNER_ROLE_ID",
            "MD_ROLE_ID",
            "ASSISTANT_ROLE_ID",
            "GUILD_ID"
        ]
        return [
            app_commands.Choice(name=k, value=k)
            for k in known_keys if current.lower() in k.lower()
        ][:25]

    @app_commands.command(name="config", description="แผงตั้งค่าระบบแบบ Realtime (ไม่ต้องรีสตาร์ตบอท)")
    async def config_panel(self, interaction: discord.Interaction):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)
        await self._send_config_panel(interaction)

    @app_commands.command(name="set_config", description="ตั้งค่าตัวแปรใน config (หรือเว้นว่างเพื่อเปิดดูแผงตั้งค่า)")
    @app_commands.describe(
        key="ชื่อตัวแปรใน config (เช่น OPERATION_OTP_CHANNEL, AUTO_STOP_TIMES)",
        value="ค่าใหม่ (ถ้าเว้นว่างจะเปิดหน้าต่าง Modal ให้กรอก)"
    )
    async def set_config_command(self, interaction: discord.Interaction, key: Optional[str] = None, value: Optional[str] = None):
        await self._handle_set_config(interaction, key, value)

    @set_config_command.autocomplete("key")
    async def set_config_key_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return await self._key_autocomplete(interaction, current)

    async def _safe_reload_or_load(self, cog_name: str) -> str:
        """รีโหลดโมดูลอย่างปลอดภัย หรือโหลดเพิ่มทันทีหากเป็นโมดูลใหม่ที่เพิ่งถูกดึงเข้ามา"""

        if cog_name in self.bot.extensions:
            try:
                await self.bot.reload_extension(cog_name)
                return f"✅ `{cog_name}`: รีโหลดสำเร็จ"
            except Exception as e:
                return f"❌ `{cog_name}`: ล้มเหลว ({e})"
        else:
            try:
                await self.bot.load_extension(cog_name)
                return f"✅ `{cog_name}`: โหลดโมดูลใหม่เข้า Memory สำเร็จ"
            except Exception as e:
                return f"❌ `{cog_name}`: โหลดล้มเหลว ({e})"

    @app_commands.command(name="reload", description="[Admin] โหลดโมดูล Cog ใหม่ทันที (ไม่ต้องรีสตาร์ตบอท)")
    @app_commands.describe(cog_name="โมดูลที่ต้องการรีโหลด (ค่าเริ่มต้น: รีโหลดทั้งหมด)")
    @app_commands.choices(cog_name=[
        app_commands.Choice(name="ทั้งหมด (All Modules)", value="all"),
        app_commands.Choice(name="Welcome / Intro (ระบบต้อนรับและกรอกข้อมูล)", value="cogs.introduction"),
        app_commands.Choice(name="Operations (ระบบปฏิบัติการ)", value="cogs.operations"),
        app_commands.Choice(name="Breaktime (ระบบลางาน)", value="cogs.breaktime"),
        app_commands.Choice(name="Duty Check (ระบบเข้า-ออกเวร)", value="cogs.duty_check"),
        app_commands.Choice(name="UI Preview (ระบบแสดงตัวอย่าง)", value="cogs.ui_preview"),
        app_commands.Choice(name="Config Cog (ระบบตั้งค่า)", value="cogs.config_cog")
    ])
    async def reload_cogs_command(self, interaction: discord.Interaction, cog_name: Optional[str] = "all"):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        cogs_to_reload = (
            ["cogs.config_cog", "cogs.operations", "cogs.introduction", "cogs.ui_preview", "cogs.breaktime", "cogs.duty_check"]
            if cog_name == "all" else [cog_name]
        )

        results = []
        for c in cogs_to_reload:
            res = await self._safe_reload_or_load(c)
            results.append(res)

        # Auto-sync commands (ล้างคำสั่ง Global เก่าทิ้งเพื่อป้องกันคำสั่งเบิ้ลซ้ำซ้อน)
        try:
            app_id = self.bot.application_id or (self.bot.user.id if self.bot.user else None)
            if app_id:
                await self.bot.http.bulk_upsert_global_commands(app_id, payload=[])

            guilds_to_sync = set()
            if interaction.guild:
                guilds_to_sync.add(interaction.guild.id)
            if config.guild_id:
                guilds_to_sync.add(config.guild_id)

            for gid in guilds_to_sync:
                g_obj = discord.Object(id=gid)
                self.bot.tree.copy_global_to(guild=g_obj)
                await self.bot.tree.sync(guild=g_obj)

            results.append("⚡ `Slash Commands`: ซิงค์คำสั่งระดับ Guild สำเร็จ และล้างชุดคำสั่ง Global ที่ซ้ำซ้อนออกแล้ว")
        except Exception as se:
            results.append(f"⚠️ `Slash Commands`: {se}")

        await interaction.followup.send("\n".join(results), ephemeral=True)

    @app_commands.command(name="sync", description="[Admin] ซิงค์ Slash Commands เข้า Guild ทันที และล้างคำสั่ง Global ที่ซ้ำซ้อน")
    async def sync_commands(self, interaction: discord.Interaction):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            # 1. ล้างคำสั่ง Global เก่าออกจาก Discord API ทั้งหมดเพื่อแก้ปัญหาคำสั่งขึ้นเบิ้ลซ้ำ 2 ชุด
            app_id = self.bot.application_id or (self.bot.user.id if self.bot.user else None)
            if app_id:
                await self.bot.http.bulk_upsert_global_commands(app_id, payload=[])

            # 2. ซิงค์เฉพาะระดับ Guild เพื่อให้คำสั่งแสดงผลชุดเดียวทันที
            guilds_to_sync = set()
            if interaction.guild:
                guilds_to_sync.add(interaction.guild.id)
            if config.guild_id:
                guilds_to_sync.add(config.guild_id)

            synced_details = []
            for gid in guilds_to_sync:
                g_obj = discord.Object(id=gid)
                self.bot.tree.copy_global_to(guild=g_obj)
                synced = await self.bot.tree.sync(guild=g_obj)
                synced_details.append(f"• Guild `{gid}`: `{len(synced)}` คำสั่ง (อัปเดตทันที)")

            details_str = "\n".join(synced_details)
            msg = f"✅ ซิงค์ Slash Commands สำเร็จ (ล้างคำสั่ง Global ที่ซ้ำซ้อนออกแล้ว):\n{details_str}\n-# หากบน Discord ยังเห็นคำสั่งเบิ้ล ให้กด Restart Discord หรือ Ctrl + R เพื่อเคลียร์แคชของแอป"
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ ซิงค์คำสั่งล้มเหลว: {e}", ephemeral=True)

    @app_commands.command(name="git_pull", description="[Admin] ดึงโค้ดล่าสุดจาก GitHub และรีโหลดบอททันที")
    async def git_pull_command(self, interaction: discord.Interaction):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "pull",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            out_str = stdout.decode().strip() or stderr.decode().strip()

            reload_results = []
            for c in ["cogs.config_cog", "cogs.operations", "cogs.introduction", "cogs.ui_preview", "cogs.breaktime", "cogs.duty_check"]:
                res = await self._safe_reload_or_load(c)
                reload_results.append(res)

            # Auto-sync commands after pull (ล้างคำสั่ง Global เก่าทิ้งเพื่อป้องกันคำสั่งเบิ้ลซ้ำซ้อน)
            try:
                app_id = self.bot.application_id or (self.bot.user.id if self.bot.user else None)
                if app_id:
                    await self.bot.http.bulk_upsert_global_commands(app_id, payload=[])

                guilds_to_sync = set()
                if interaction.guild:
                    guilds_to_sync.add(interaction.guild.id)
                if config.guild_id:
                    guilds_to_sync.add(config.guild_id)

                for gid in guilds_to_sync:
                    g_obj = discord.Object(id=gid)
                    self.bot.tree.copy_global_to(guild=g_obj)
                    await self.bot.tree.sync(guild=g_obj)

                reload_results.append("⚡ `Slash Commands`: ซิงค์คำสั่งระดับ Guild สำเร็จ และล้างชุดคำสั่ง Global ที่ซ้ำซ้อนออกแล้ว")
            except Exception as se:
                reload_results.append(f"⚠️ `Slash Commands`: {se}")

            msg = f"**Git Pull Result:**\n```\n{out_str[:1200]}\n```\n" + "\n".join(reload_results)
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ เกิดข้อผิดพลาดในการรัน git pull: {e}", ephemeral=True)

    @app_commands.command(name="restart", description="[Admin] รีสตาร์ตกระบวนการทำงานของบอทใหม่ทั้งหมด (Full Process Restart)")
    async def restart_command(self, interaction: discord.Interaction):
        if not self._check_admin(interaction):
            return await interaction.response.send_message("❌ คุณไม่มีสิทธิ์เข้าถึงคำสั่งนี้", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        now_ts = int(datetime.now().timestamp())
        embed = discord.Embed(
            title="🔄 กำลังรีสตาร์ตบอท...",
            description=(
                f"**คำสั่งโดย:** {interaction.user.mention}\n"
                f"• ระบบกำลังปิดการเชื่อมต่อและเริ่มโปรเซสใหม่ทั้งหมด\n"
                f"• บอทจะกลับมาออนไลน์ใหม่อัตโนมัติในอีกสักครู่ (~5-10 วินาที)\n\n"
                f"-# กรุณารอสักครู่ก่อนใช้งานคำสั่งถัดไป"
            ),
            color=discord.Color.from_rgb(245, 158, 11),
            timestamp=datetime.now(config.timezone)
        )
        embed.set_footer(text="What Universal System Service")
        await interaction.followup.send(embed=embed, ephemeral=True)

        logger.info(f"[ConfigCog] Bot restart initiated by {interaction.user} ({interaction.user.id})")

        # รอ 1.5 วินาทีเพื่อให้ส่งข้อความใน Discord เสร็จสมบูรณ์
        await asyncio.sleep(1.5)

        # ปิดการเชื่อมต่อของบอทอย่างปลอดภัย
        try:
            await self.bot.close()
        except Exception:
            pass

        # เริ่มโปรเซสใหม่และปิดตัวเดิม
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            logger.error(f"[ConfigCog] os.execv failed: {e}, falling back to subprocess")
            subprocess.Popen([sys.executable] + sys.argv)
            sys.exit(0)

async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot))
