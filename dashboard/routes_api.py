import os
import io
import shutil
import tempfile
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
import discord
from aiohttp import web
from openpyxl import Workbook

from config_manager import config
from services.storage_service import (
    load_operation_status, save_operation_status,
    load_operation_daily_data, save_operation_session_data,
    read_leave_db, write_leave_db, sanitize_iso_date, parse_smart_date,
    CURRENT_DUTIES_FILE, PENDING_ALERTS_FILE, DATA_DIR, read_json_file
)
from services import leave_service
from dashboard.auth import get_session_user

routes = web.RouteTableDef()

def require_admin(handler):
    """Decorator ป้องกัน API ต้องล็อกอินเป็น Admin ก่อนเสมอ"""
    async def wrapper(request: web.Request):
        user = get_session_user(request)
        if not user:
            return web.json_response({"success": False, "error": "Unauthorized: กรุณาเข้าสู่ระบบก่อน"}, status=401)
        return await handler(request, user)
    return wrapper

def format_duration(total_seconds: int) -> str:
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours > 0:
        parts.append(f"{hours} ชม.")
    if minutes > 0 or hours > 0:
        parts.append(f"{minutes} นาที")
    parts.append(f"{seconds} วินาที")
    return " ".join(parts)

# --- 1. Overview & System Health ---
@routes.get("/api/overview")
@require_admin
async def api_overview(request: web.Request, user: dict):
    bot = request.app["bot"]
    guild = bot.get_guild(config.guild_id)
    now_dt = datetime.now(config.timezone)

    # ข้อมูลสถานะบอท
    bot_info = {
        "name": str(bot.user) if bot.user else "Offline",
        "avatar": str(bot.user.display_avatar.url) if bot.user else "",
        "latency_ms": round(bot.latency * 1000, 1) if bot.latency else 0,
        "guild_name": guild.name if guild else "What Universal",
        "member_count": guild.member_count if guild else 0,
        "version": config.get("BOT_VERSION", "v1.0.0"),
        "timezone": str(config.timezone)
    }

    # ข้อมูล Operations
    op_status = await load_operation_status()
    is_op_active = bool(op_status.get("status_message_id"))
    active_op_users = [k for k in op_status.keys() if isinstance(k, str) and k.isdigit()]
    start_time_str = op_status.get("start_time")
    op_elapsed_sec = 0
    if start_time_str:
        try:
            st_dt = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
            op_elapsed_sec = int((now_dt - st_dt).total_seconds())
        except Exception:
            pass

    operations_summary = {
        "is_active": is_op_active,
        "active_users_count": len(active_op_users),
        "start_time": start_time_str.split(".")[0] if start_time_str else None,
        "elapsed_seconds": max(0, op_elapsed_sec),
        "elapsed_formatted": format_duration(op_elapsed_sec),
        "stop_time": op_status.get("temp_auto_stop") or op_status.get("stop_time"),
        "otp": op_status.get("otp", "-")
    }

    # ข้อมูลการลางาน (Leave System)
    leave_data = await read_leave_db()
    today = now_dt.date()
    max_quota = int(config.get("MAX_LEAVE_PER_DAY") or 5)
    today_count = 0
    active_leaves = 0
    upcoming_leaves = 0

    for r in leave_data.get("leave_records", []):
        if r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
            try:
                st_d = date.fromisoformat(r["start_date"])
                en_d = date.fromisoformat(r["end_date"])
                if st_d <= today <= en_d:
                    active_leaves += 1
                    if r.get("leave_type") != "BYPASS":
                        today_count += 1
                elif st_d > today:
                    upcoming_leaves += 1
            except Exception:
                pass

    leaves_summary = {
        "active_leaves": active_leaves,
        "upcoming_leaves": upcoming_leaves,
        "today_count": today_count,
        "max_quota": max_quota,
        "remaining_quota": max(0, max_quota - today_count)
    }

    # ข้อมูล Duty Check
    duties_data = await read_json_file(CURRENT_DUTIES_FILE, {}) or {}
    on_duty_count = sum(1 for v in duties_data.values() if v.get("last_status") == "on_duty")
    off_duty_count = sum(1 for v in duties_data.values() if v.get("last_status") == "off_duty")
    alerts_data = await read_json_file(PENDING_ALERTS_FILE, {}) or {}

    duties_summary = {
        "on_duty_count": on_duty_count,
        "off_duty_count": off_duty_count,
        "pending_alerts_count": len(alerts_data)
    }

    return web.json_response({
        "success": True,
        "bot": bot_info,
        "operations": operations_summary,
        "leaves": leaves_summary,
        "duties": duties_summary,
        "current_user": user
    })

# --- 2. Operations & Time Tracker API ---
@routes.get("/api/operations/status")
@require_admin
async def api_operations_status(request: web.Request, user: dict):
    bot = request.app["bot"]
    guild = bot.get_guild(config.guild_id)
    op_status = await load_operation_status()
    now_dt = datetime.now(config.timezone)

    role_mapping = config.role_mapping
    unit_groups: Dict[str, List[Dict[str, Any]]] = {name: [] for name in role_mapping.values()}
    unit_groups["หน่วยอื่น ๆ / ไม่ระบุ"] = []

    for k, v in op_status.items():
        if isinstance(k, str) and k.isdigit():
            user_id = k
            pname = v.get("player_name", f"User {k}")
            start_str = v.get("start_time", "")
            duration_sec = 0
            if start_str:
                try:
                    st_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
                    duration_sec = int((now_dt - st_dt).total_seconds())
                except Exception:
                    pass

            matched_unit = "หน่วยอื่น ๆ / ไม่ระบุ"
            avatar_url = ""
            member = guild.get_member(int(k)) if guild else None
            if member:
                avatar_url = str(member.display_avatar.url)
                pname = member.display_name
                for rid, rname in role_mapping.items():
                    if member.get_role(rid):
                        matched_unit = rname
                        break

            unit_groups[matched_unit].append({
                "user_id": user_id,
                "player_name": pname,
                "avatar": avatar_url,
                "start_time": start_str.split(".")[0] if start_str else "-",
                "duration_seconds": max(0, duration_sec),
                "duration_formatted": format_duration(duration_sec)
            })

    start_str = op_status.get("start_time")
    round_elapsed = 0
    if start_str:
        try:
            st = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
            round_elapsed = int((now_dt - st).total_seconds())
        except Exception:
            pass

    return web.json_response({
        "success": True,
        "is_active": bool(op_status.get("status_message_id")),
        "start_time": start_str.split(".")[0] if start_str else None,
        "elapsed_seconds": max(0, round_elapsed),
        "stop_time": op_status.get("temp_auto_stop") or op_status.get("stop_time"),
        "otp": op_status.get("otp", "-"),
        "otp_expires_at": op_status.get("otp_expires_at"),
        "units": unit_groups
    })

@routes.post("/api/operations/start")
@require_admin
async def api_operations_start(request: web.Request, user: dict):
    bot = request.app["bot"]
    data = await request.json() if request.can_read_body else {}
    stop_time = data.get("stop_time", "").strip() or None

    cog = bot.get_cog("OperationsCog")
    if not cog:
        return web.json_response({"success": False, "error": "OperationsCog ไม่ได้ถูกโหลด"}, status=500)

    # สั่งเริ่มผ่าน Cog Logic
    op_status = await load_operation_status()
    if op_status.get("status_message_id"):
        return web.json_response({"success": False, "error": "มีรอบบันทึกเวลาที่เปิดใช้งานอยู่แล้ว กรุณาสั่งปิดก่อน"}, status=400)

    # ส่งแผงไปยังช่อง OPERATIONS
    log_ch_id = config.get("OPERATION_LOG_CHANNEL_ID")
    if not log_ch_id:
        return web.json_response({"success": False, "error": "ยังไม่ได้ตั้งค่า OPERATION_LOG_CHANNEL_ID ใน config"}, status=400)

    ch = await cog.get_channel(log_ch_id)
    if not ch:
        return web.json_response({"success": False, "error": f"ไม่พบช่องแชท ID: {log_ch_id}"}, status=400)

    try:
        now_dt = datetime.now(config.timezone)
        from cogs.operations import get_next_auto_stop_ts, generate_otp, OperationView

        auto_stop_ts = get_next_auto_stop_ts(stop_time)
        auto_stop_val = f"<t:{auto_stop_ts}:t> (<t:{auto_stop_ts}:R>)"
        otp_ch_id = config.get("OPERATION_OTP_CHANNEL")
        otp_val = f"ดูรหัสรอบปัจจุบันได้ที่ห้อง <#{otp_ch_id}>" if otp_ch_id else "ดูรหัสที่ห้อง OTP"

        # ปิดแผงเก่าที่ค้างอยู่ในห้อง
        try:
            from cogs.operations import DisabledOperationView
            async for old_msg in ch.history(limit=35):
                if old_msg.author.id == bot.user.id and old_msg.embeds:
                    if "WHAT UNIVERSAL — TIME TRACKER" in (old_msg.embeds[0].title or ""):
                        try:
                            await old_msg.edit(view=DisabledOperationView())
                        except Exception:
                            pass
        except Exception:
            pass

        embed = discord.Embed(
            title="WHAT UNIVERSAL — TIME TRACKER",
            description="-# ระบบลงเวลาทำงานและตรวจสอบรายชื่อคนออนไลน์",
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=now_dt
        )
        embed.add_field(name="สถานะระบบ", value="`Active` (เปิดรับลงเวลา)", inline=True)
        embed.add_field(name="ตัดรอบอัตโนมัติ", value=auto_stop_val, inline=True)
        embed.add_field(name="รหัส OTP", value=otp_val, inline=False)
        embed.add_field(name="รายชื่อคนที่ Check-in อยู่", value="*— ไม่มีรายชื่อในขณะนี้ —*", inline=False)
        embed.set_footer(text="What Universal • อัปเดตข้อมูลอัตโนมัติ")

        status_msg = await ch.send(embed=embed, view=OperationView())

        otp_min = int(config.get("OTP_EXPIRY_MINUTES", 20))
        first_otp = generate_otp()
        first_exp = now_dt + timedelta(minutes=otp_min)

        new_status = {
            "start_time": now_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
            "stop_time": stop_time,
            "auto_stop_ts": auto_stop_ts,
            "status_message_id": status_msg.id,
            "status_channel_id": ch.id,
            "button_messages": [{"channel_id": ch.id, "message_id": status_msg.id}],
            "recent_sessions": [],
            "otp_verified_users": {},
            "otp": first_otp,
            "otp_generated_at": now_dt.isoformat(),
            "otp_expires_at": first_exp.isoformat()
        }
        if stop_time:
            new_status["temp_auto_stop"] = stop_time

        # ส่ง OTP เข้าห้อง
        if otp_ch_id:
            otp_ch = await cog.get_channel(otp_ch_id)
            if otp_ch:
                otp_emb = discord.Embed(
                    title="OTP PASSCODE — TIME TRACKER",
                    description=f"รหัสสำหรับใช้ Check-in ประจำรอบนี้:\n\n# `{first_otp}`\n\n• เปลี่ยนรหัสทุก: `{otp_min} นาที`\n• หมดอายุใน: <t:{int(first_exp.timestamp())}:R>\n\n-# กรุณานำรหัส 4 หลักไปกรอกในปุ่ม Check-in เพื่อเริ่มบันทึกเวลา",
                    color=discord.Color.from_rgb(88, 101, 242),
                    timestamp=now_dt
                )
                otp_emb.set_footer(text="What Universal OTP Service")
                otp_msg = await otp_ch.send(embed=otp_emb)
                new_status["otp_message_id"] = otp_msg.id

        await save_operation_status(new_status)

        await cog.send_assistant_log(
            "SESSION OPENED (VIA DASHBOARD)",
            f"ผู้เปิดรอบ: **{user.get('username')}** (<@{user.get('user_id')}>)\nเวลาเริ่ม: `{now_dt.strftime('%H:%M:%S')}`\nเวลาปิดที่ตั้งไว้: `{stop_time or 'ตามระบบ'}`",
            color=discord.Color.from_rgb(88, 101, 242)
        )

        return web.json_response({"success": True, "message": "เปิดรอบบันทึกเวลาเรียบร้อยแล้ว"})
    except Exception as e:
        logger.error(f"Error starting operation via dashboard: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

@routes.post("/api/operations/stop")
@require_admin
async def api_operations_stop(request: web.Request, user: dict):
    bot = request.app["bot"]
    cog = bot.get_cog("OperationsCog")
    if not cog:
        return web.json_response({"success": False, "error": "OperationsCog ไม่ได้ถูกโหลด"}, status=500)

    try:
        await cog.execute_stop(interaction=None, source=f"dashboard_by:{user.get('user_id')}")
        return web.json_response({"success": True, "message": "สั่งปิดรอบและสรุปผลเรียบร้อยแล้ว"})
    except Exception as e:
        logger.error(f"Error stopping operation via dashboard: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

@routes.post("/api/operations/force_stop")
@require_admin
async def api_operations_force_stop(request: web.Request, user: dict):
    bot = request.app["bot"]
    data = await request.json()
    target_uid = str(data.get("user_id", "")).strip()
    if not target_uid:
        return web.json_response({"success": False, "error": "กรุณาระบุ user_id"}, status=400)

    cog = bot.get_cog("OperationsCog")
    op_status = await load_operation_status()
    if target_uid not in op_status or "start_time" not in op_status[target_uid]:
        return web.json_response({"success": False, "error": "ผู้ใช้นี้ไม่ได้กำลังลงเวลาอยู่ในรอบปัจจุบัน"}, status=400)

    now_dt = datetime.now(config.timezone)
    start_str = op_status[target_uid].get("start_time")
    try:
        st_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=config.timezone)
        duration = int((now_dt - st_dt).total_seconds())
    except Exception:
        duration = 0

    pname = op_status[target_uid].get("player_name", f"User {target_uid}")
    await save_operation_session_data(
        user_id=target_uid,
        player_name=pname,
        start=start_str,
        end=now_dt.strftime("%Y-%m-%d %H:%M:%S.%f"),
        duration=duration,
        source=f"dashboard_force_stop_by:{user.get('user_id')}"
    )

    del op_status[target_uid]
    await save_operation_status(op_status)
    if cog and hasattr(cog, "update_status_message"):
        await cog.update_status_message()

    return web.json_response({
        "success": True,
        "message": f"สั่งให้ {pname} ออกเวรเรียบร้อยแล้ว (รวมเวลา: {format_duration(duration)})"
    })

@routes.get("/api/operations/daily")
@require_admin
async def api_operations_daily(request: web.Request, user: dict):
    date_query = request.query.get("date", "").strip() or "วันนี้"
    parsed_d = parse_smart_date(date_query, config.timezone)
    if not parsed_d:
        return web.json_response({"success": False, "error": "รูปแบบวันที่ไม่ถูกต้อง"}, status=400)

    daily_data = await load_operation_daily_data(datetime.combine(parsed_d, datetime.min.time()))
    results = []
    total_time_sec = 0

    for uid, udata in (daily_data or {}).items():
        user_total = sum(int(s.get("duration_seconds", 0)) for s in udata.get("sessions", []))
        total_time_sec += user_total
        results.append({
            "user_id": uid,
            "player_name": udata.get("player_name", f"User {uid}"),
            "total_seconds": user_total,
            "total_formatted": format_duration(user_total),
            "sessions_count": len(udata.get("sessions", [])),
            "sessions": udata.get("sessions", [])
        })

    # เรียงลำดับเวลารวมจากมากไปน้อย
    results.sort(key=lambda x: x["total_seconds"], reverse=True)

    return web.json_response({
        "success": True,
        "date": parsed_d.isoformat(),
        "total_users": len(results),
        "total_time_seconds": total_time_sec,
        "total_time_formatted": format_duration(total_time_sec),
        "records": results
    })

@routes.get("/api/operations/export_excel")
@require_admin
async def api_operations_export_excel(request: web.Request, user: dict):
    date_query = request.query.get("date", "").strip() or "วันนี้"
    parsed_d = parse_smart_date(date_query, config.timezone)
    if not parsed_d:
        return web.HTTPBadRequest(text="รูปแบบวันที่ไม่ถูกต้อง")

    query_date = datetime.combine(parsed_d, datetime.min.time())
    daily_data = await load_operation_daily_data(query_date)

    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Summary"
    ws.append(["Discord ID", "Player Name", "Start Time", "End Time", "Duration (Sec)", "Duration (Formatted)", "Source"])

    for uid, udata in (daily_data or {}).items():
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

    filename = f"operation_report_{parsed_d.strftime('%Y%m%d')}.xlsx"
    return web.Response(
        body=excel_buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# --- 3. Leave & Breaktime Management API ---
@routes.get("/api/leaves")
@require_admin
async def api_leaves(request: web.Request, user: dict):
    data = await read_leave_db()
    today = datetime.now(config.timezone).date()
    records = data.get("leave_records", [])

    active = []
    upcoming = []
    history = []

    for r in records:
        st = r.get("start_date", "")
        en = r.get("end_date", "")
        dur = r.get("duration_days", 1)
        st_d = date.fromisoformat(st) if st else today
        en_d = date.fromisoformat(en) if en else today

        rem_days = max(0, (en_d - today).days + 1)
        record_obj = {
            "record_id": r.get("record_id", "-"),
            "user_id": r.get("user_id"),
            "user_name": r.get("user_name", f"User {r.get('user_id')}"),
            "leave_type": r.get("leave_type", "GENERAL"),
            "status": r.get("status", "ON_LEAVE"),
            "start_date": st,
            "end_date": en,
            "duration_days": dur,
            "remaining_days": rem_days,
            "reason": r.get("reason", "-")
        }

        if r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
            if st_d <= today <= en_d:
                active.append(record_obj)
            elif st_d > today:
                upcoming.append(record_obj)
            else:
                history.append(record_obj)
        else:
            history.append(record_obj)

    max_quota = int(config.get("MAX_LEAVE_PER_DAY") or 5)
    today_count = sum(1 for a in active if a.get("leave_type") != "BYPASS")

    return web.json_response({
        "success": True,
        "today_count": today_count,
        "max_quota": max_quota,
        "remaining_quota": max(0, max_quota - today_count),
        "active": active,
        "upcoming": upcoming,
        "history": history
    })

@routes.post("/api/leaves/cancel")
@require_admin
async def api_leaves_cancel(request: web.Request, user: dict):
    bot = request.app["bot"]
    data = await request.json()
    target_uid = str(data.get("user_id", "")).strip()
    if not target_uid:
        return web.json_response({"success": False, "error": "กรุณาระบุ user_id"}, status=400)

    is_success, msg = await leave_service.cancel_leave_request(
        user_id_requesting=str(user.get("user_id")),
        user_id_to_cancel=target_uid,
        is_admin=True,
        allow_active=True
    )

    if is_success:
        cog = bot.get_cog("BreaktimeCog")
        if cog:
            await cog.update_leave_board()
        return web.json_response({"success": True, "message": msg})
    else:
        return web.json_response({"success": False, "error": msg}, status=400)

@routes.post("/api/leaves/add_bypass")
@require_admin
async def api_leaves_add_bypass(request: web.Request, user: dict):
    bot = request.app["bot"]
    data = await request.json()
    target_uid = str(data.get("user_id", "")).strip()
    start_date = str(data.get("start_date", "")).strip()
    end_date = str(data.get("end_date", "")).strip()
    reason = str(data.get("reason", "ยื่นลาผ่าน Dashboard")).strip()

    if not target_uid or not start_date or not end_date:
        return web.json_response({"success": False, "error": "กรุณากรอกข้อมูลให้ครบถ้วน"}, status=400)

    # แปลงและตรวจวันที่
    p_st = parse_smart_date(start_date, config.timezone)
    p_en = parse_smart_date(end_date, config.timezone)
    if not p_st or not p_en or p_st > p_en:
        return web.json_response({"success": False, "error": "ช่วงวันที่ไม่ถูกต้อง"}, status=400)

    guild = bot.get_guild(config.guild_id)
    member = guild.get_member(int(target_uid)) if guild else None
    user_name = member.display_name if member else f"User {target_uid}"

    is_success, msg = await leave_service.process_bypass_leave(
        admin_id=str(user.get("user_id")),
        target_user_id=target_uid,
        user_name=user_name,
        start_date=p_st.isoformat(),
        end_date=p_en.isoformat(),
        reason=reason
    )

    if is_success:
        cog = bot.get_cog("BreaktimeCog")
        if cog:
            await cog.update_leave_board()
        return web.json_response({"success": True, "message": msg})
    else:
        return web.json_response({"success": False, "error": msg}, status=400)

# --- 4. Duty & Patrol Check API ---
@routes.get("/api/duties")
@require_admin
async def api_duties(request: web.Request, user: dict):
    bot = request.app["bot"]
    guild = bot.get_guild(config.guild_id)
    duties_data = await read_json_file(CURRENT_DUTIES_FILE, {}) or {}
    alerts_data = await read_json_file(PENDING_ALERTS_FILE, {}) or {}

    on_duty_list = []
    off_duty_list = []

    for uid, d in duties_data.items():
        member = guild.get_member(int(uid)) if guild else None
        pname = member.display_name if member else d.get("player_name", f"User {uid}")
        avatar = str(member.display_avatar.url) if member else ""

        item = {
            "user_id": uid,
            "player_name": pname,
            "avatar": avatar,
            "last_status": d.get("last_status"),
            "last_status_time": d.get("last_status_time"),
            "message_link": d.get("last_status_message_link")
        }
        if d.get("last_status") == "on_duty":
            on_duty_list.append(item)
        else:
            off_duty_list.append(item)

    return web.json_response({
        "success": True,
        "on_duty": on_duty_list,
        "off_duty": off_duty_list,
        "pending_alerts": alerts_data
    })

# --- 5. Configuration & System Settings API ---
@routes.get("/api/config")
@require_admin
async def api_get_config(request: web.Request, user: dict):
    # คัดลอกค่าและซ่อน Secret ที่เป็นความลับ
    cfg_copy = dict(config._config)
    return web.json_response({
        "success": True,
        "config": cfg_copy
    })

@routes.post("/api/config")
@require_admin
async def api_save_config(request: web.Request, user: dict):
    data = await request.json()
    allowed_keys = [
        "GUILD_ID", "WEB_PORT", "TIMEZONE", "AUTO_STOP_TIMES", "OTP_EXPIRY_MINUTES",
        "OPERATION_LOG_CHANNEL_ID", "OPERATION_OTP_CHANNEL", "OPERATION_LOG_ASSISTANT_CHANNEL_ID",
        "OPERATION_PERSONAL_LOG_CHANNELS", "LEAVE_BOARD_CHANNEL_ID", "LEAVE_PANEL_CHANNEL_ID",
        "MAX_LEAVE_PER_DAY", "MAX_LEAVES_SAME_DAY", "ALERT_CHANNEL_ID", "MAX_OFF_DUTY_GAP_SECONDS",
        "DUTY_TRACKER_ENABLED", "WELCOME_TICKET_ENABLED", "ADMIN_IDS", "SUPER_ASSISTANT", "ASSISTANT",
        "ROLE_MAPPING", "ADMIN_PASSCODE", "DISCORD_CLIENT_SECRET", "DASHBOARD_URL"
    ]

    updated = 0
    for k, v in data.items():
        if k in allowed_keys:
            config._config[k] = v
            updated += 1

    await config.save()
    return web.json_response({
        "success": True,
        "message": f"บันทึกการตั้งค่า {updated} รายการเรียบร้อยแล้ว (มีผลทันที)"
    })

@routes.post("/api/system/backup")
@require_admin
async def api_system_backup(request: web.Request, user: dict):
    try:
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp_path = tmp.name
        tmp.close()
        zip_base = tmp_path[:-4]
        await asyncio.to_thread(shutil.make_archive, zip_base, 'zip', DATA_DIR)

        with open(tmp_path, "rb") as f:
            file_bytes = f.read()

        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"what_universal_backup_{now_str}.zip"
        return web.Response(
            body=file_bytes,
            content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error(f"Backup failed: {e}", exc_info=True)
        return web.json_response({"success": False, "error": str(e)}, status=500)

@routes.get("/api/members")
@require_admin
async def api_members(request: web.Request, user: dict):
    """ค้นหารายชื่อสมาชิกในเซิร์ฟเวอร์สำหรับช่องเลือกคนในฟอร์มต่างๆ"""
    bot = request.app["bot"]
    guild = bot.get_guild(config.guild_id)
    if not guild:
        return web.json_response({"success": True, "members": []})

    q = request.query.get("q", "").strip().lower()
    results = []

    for m in guild.members:
        if m.bot:
            continue
        pname = m.display_name
        uname = m.name
        if q and q not in pname.lower() and q not in uname.lower() and q not in str(m.id):
            continue
        results.append({
            "id": str(m.id),
            "name": pname,
            "username": uname,
            "avatar": str(m.display_avatar.url)
        })
        if len(results) >= 50:
            break

    return web.json_response({"success": True, "members": results})
