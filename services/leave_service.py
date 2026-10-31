import os
from datetime import date, timedelta, datetime
from dateutil.relativedelta import relativedelta
from typing import Optional, Tuple, List, Dict, Any, Set

from config_manager import config
from services.storage_service import read_leave_db, write_leave_db, AUTHORIZED_USERS_DIR

def find_active_record(all_records: list, user_id: str) -> Optional[dict]:
    u_str = str(user_id)
    for record in all_records:
        if str(record.get("user_id")) == u_str and record.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
            return record
    return None

def get_next_l7_available_date(return_date: date) -> date:
    next_month = return_date + relativedelta(months=1)
    day = return_date.day
    if 1 <= day <= 17:
        return next_month.replace(day=1)
    elif 18 <= day <= 23:
        return next_month.replace(day=5)
    elif 24 <= day <= 28:
        return next_month.replace(day=10)
    elif 29 <= day <= 31:
        return next_month.replace(day=15)
    return next_month.replace(day=1)

def read_authorized_users(month_key: str) -> Set[str]:
    """อ่านไฟล์ .txt ของผู้มีสิทธิ์ลาประจำเดือน เช่น 10_2025.txt"""
    filename = os.path.join(AUTHORIZED_USERS_DIR, f"{month_key}.txt")
    user_ids = set()
    try:
        if not os.path.exists(filename):
            print(f"[LeaveService] WARN: ไม่พบไฟล์ผู้มีสิทธิ์: {filename}")
            return set()
        with open(filename, 'r', encoding='utf-8') as f:
            for line in f:
                uid = line.strip()
                if uid.isdigit():
                    user_ids.add(uid)
        print(f"[LeaveService] INFO: อ่าน {filename} พบผู้มีสิทธิ์ {len(user_ids)} คน")
        return user_ids
    except Exception as e:
        print(f"[LeaveService] ERROR: อ่านไฟล์ {filename} ล้มเหลว: {e}")
        return set()

def get_master_cooldown_date(all_records: list, user_data: dict, user_id: str) -> date:
    """คำนวณวันที่พ้นคูลดาวน์ที่ไกลที่สุด"""
    user_state = user_data.get(user_id, {})
    next_l7_str = user_state.get("next_l7_available_date", "1970-01-01")
    try:
        l7_cooldown_date = date.fromisoformat(next_l7_str)
    except Exception:
        l7_cooldown_date = date.fromisoformat("1970-01-01")

    last_l3_4_end = None
    last_bypass_end = None

    for record in all_records:
        if record.get("user_id") == user_id and record.get("status") == "COMPLETED":
            end_str = record.get("end_date")
            if not end_str:
                continue
            try:
                rec_end = date.fromisoformat(end_str)
            except Exception:
                continue

            if record.get("leave_type") == "L_3_4":
                if last_l3_4_end is None or rec_end > last_l3_4_end:
                    last_l3_4_end = rec_end
            elif record.get("leave_type") == "BYPASS":
                if last_bypass_end is None or rec_end > last_bypass_end:
                    last_bypass_end = rec_end

    l3_4_cooldown = date.fromisoformat("1970-01-01")
    if last_l3_4_end is not None:
        l3_4_cooldown = last_l3_4_end + timedelta(days=8)

    bypass_cooldown = date.fromisoformat("1970-01-01")
    if last_bypass_end is not None:
        bypass_cooldown = last_bypass_end + timedelta(days=31)

    return max(l7_cooldown_date, l3_4_cooldown, bypass_cooldown)

def find_next_available_l7_slot(all_records: list, start_search_date: date) -> Optional[date]:
    """ค้นหาหน้าต่าง 7 วันถัดไปที่ไม่เกินโควต้าซ้อนทับ"""
    max_overlap = int(config.get("MAX_OVERLAP_L7", 10))
    max_overlap_days = int(config.get("MAX_OVERLAP_DAYS_L7", 1))

    l7_records = [
        r for r in all_records
        if r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"] and r.get("leave_type") == "L_7"
    ]

    for i in range(60):
        current_start = start_search_date + timedelta(days=i)
        current_end = current_start + timedelta(days=6)

        days_full_count = 0
        check_date = current_start
        while check_date <= current_end:
            overlap = 0
            for r in l7_records:
                try:
                    r_start = date.fromisoformat(r["start_date"])
                    r_end = date.fromisoformat(r["end_date"])
                    if r_start <= check_date <= r_end:
                        overlap += 1
                except Exception:
                    continue

            if overlap >= max_overlap:
                days_full_count += 1
            check_date += timedelta(days=1)

        if days_full_count <= max_overlap_days:
            return current_start

    return None

async def perform_return_pre_check(user_id: str) -> Tuple[bool, str]:
    all_data = await read_leave_db()
    all_records = all_data.get("leave_records", [])
    today_str = datetime.now(config.timezone).date().isoformat()

    record_to_check = None
    for r in all_records:
        if r.get("user_id") == user_id and r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]:
            record_to_check = r
            break

    if record_to_check is None:
        return (False, "ไม่พบข้อมูลการลาที่ 'กำลังลาอยู่' (ON_LEAVE) หรือ 'หมดอายุ' (EXPIRED) ของคุณ")

    status = record_to_check.get("status")
    if status == "EXPIRED_PENDING_RETURN":
        return (True, "OK")
    if status == "ON_LEAVE":
        start_date = record_to_check.get("start_date", "9999-12-31")
        if start_date <= today_str:
            return (True, "OK")
        else:
            return (False, "คุณยังไม่ถึงวันลา (สถานะ: 'รอคิว') ไม่สามารถกลับเข้าเวรล่วงหน้าได้")
    return (False, "ไม่พบสถานะการลาที่สามารถกลับเข้าเวรได้")

async def perform_pre_checks(user_id: str, user_role_ids: list, leave_type: str) -> Tuple[bool, str, int]:
    """ตรวจสอบสิทธิ์เบื้องต้นก่อนเปิด Modal ขอลางาน"""
    # 0. ตรวจสอบ Feature Flag
    if leave_type == "L_7" and not config.get("FEATURE_L_7_ENABLED", True):
        return (False, "การลาประเภท 7 วัน (เหมา) ถูกปิดใช้งานชั่วคราว", 0)
    if leave_type == "L_3_4" and not config.get("FEATURE_L_3_4_ENABLED", True):
        return (False, "การลาประเภท 4/3 วัน (ย่อย) ถูกปิดใช้งานชั่วคราว", 0)

    all_data = await read_leave_db()
    all_records = all_data.get("leave_records", [])
    user_data = all_data.get("user_data", {})

    # 1. ตรวจสอบว่ามีลาค้างอยู่หรือไม่
    if find_active_record(all_records, user_id):
        return (False, "คุณมีสถานะ 'กำลังลา' หรือ 'หมดอายุ (รอกลับ)' ค้างอยู่ในระบบ", 0)

    # 2. ตรวจสอบสิทธิ์จาก Role ID
    required_roles = config.get("LEAVE_BOT_REQUIRED_ROLE_IDS", [])
    if required_roles and not any(r in required_roles for r in user_role_ids):
        return (False, f"คุณไม่มียศที่กำหนด (ต้องมีอย่างน้อย 1 ยศจาก {len(required_roles)} ยศที่อนุญาต)", 0)

    # 3. ตรวจสอบรายชื่อผู้มีสิทธิ์จากไฟล์ .txt ประจำเดือน
    today = datetime.now(config.timezone).date()
    first_day_this_month = today.replace(day=1)
    last_day_prev_month = first_day_this_month - timedelta(days=1)
    month_key = last_day_prev_month.strftime("%m_%Y")

    authorized_users = read_authorized_users(month_key)
    if not authorized_users:
        return (False, f"ระบบขัดข้อง: Admin ยังไม่ได้อัปโหลดไฟล์ผู้มีสิทธิ์ '{month_key}.txt'", 0)

    if user_id not in authorized_users:
        return (False, f"คุณไม่อยู่ในรายชื่อผู้มีสิทธิ์ลางานสำหรับรอบเดือนนี้ ({month_key})", 0)

    # 4. ตรวจสอบ Cooldown
    master_cooldown = get_master_cooldown_date(all_records, user_data, user_id)
    if today < master_cooldown:
        return (False, f"คุณยังอยู่ในช่วงคูลดาวน์! (ลาได้อีกครั้งวันที่ {master_cooldown.strftime('%d/%m/%Y')})", 0)

    # 5. ตรวจสอบโควต้ารายเดือน
    current_month_str = today.strftime("%Y-%m")
    leave_type_taken_this_month = None

    for r in all_records:
        if r.get("user_id") == user_id and r.get("status") != "CANCELLED":
            try:
                r_start = date.fromisoformat(r["start_date"])
                if r_start.strftime("%Y-%m") == current_month_str:
                    if r.get("leave_type") in ["L_7", "L_3_4"]:
                        leave_type_taken_this_month = r["leave_type"]
                        break
            except Exception:
                continue

    if leave_type == "L_7":
        if leave_type_taken_this_month in ["L_7", "L_3_4"]:
            return (False, f"คุณใช้โควต้าลา (ประเภท {leave_type_taken_this_month}) ของเดือนนี้ไปแล้ว", 0)
        return (True, "OK", 7)

    elif leave_type == "L_3_4":
        if leave_type_taken_this_month == "L_7":
            return (False, "คุณใช้โควต้าลา (ประเภท L_7) ของเดือนนี้ไปแล้ว", 0)

        monthly_l3_4_days = 0
        for r in all_records:
            if r.get("user_id") == user_id and r.get("leave_type") == "L_3_4" and r.get("status") == "COMPLETED":
                try:
                    r_start = date.fromisoformat(r["start_date"])
                    if r_start.strftime("%Y-%m") == current_month_str:
                        r_end = date.fromisoformat(r["end_date"])
                        monthly_l3_4_days += (r_end - r_start).days + 1
                except Exception:
                    continue

        if monthly_l3_4_days == 0:
            duration = 4
        elif monthly_l3_4_days == 4:
            duration = 3
        else:
            return (False, f"โควต้ารายเดือนของคุณไม่พอ (ใช้ไปแล้ว {monthly_l3_4_days}/7 วัน)", 0)

        return (True, "OK", duration)

    return (False, "ไม่รู้จักประเภทการลา", 0)

async def process_leave_request(user_id: str, request_start_date: date, duration_days: int, leave_type: str) -> Tuple[bool, str]:
    """ตรวจสอบหลังกรอก Modal และบันทึกคำขอลา"""
    request_end_date = request_start_date + timedelta(days=duration_days - 1)
    all_data = await read_leave_db()
    all_records = all_data.get("leave_records", [])
    user_data = all_data.get("user_data", {})

    # Cooldown กับวันที่ขอ
    master_cooldown = get_master_cooldown_date(all_records, user_data, user_id)
    if request_start_date < master_cooldown:
        return (False, f"ไม่อนุมัติ: วันที่คุณขอ ({request_start_date.strftime('%d/%m/%Y')}) ยังอยู่ในช่วงคูลดาวน์ (ลาได้อีกครั้งวันที่ {master_cooldown.strftime('%d/%m/%Y')})")

    # ตรวจสอบโควต้าวันเริ่มลาเดียวกัน (BYPASS ไม่กินโควต้า)
    active_leaves = [r for r in all_records if r.get("status") in ["ON_LEAVE", "EXPIRED_PENDING_RETURN"]]
    max_same_day = int(config.get("MAX_LEAVE_PER_DAY") or config.get("MAX_LEAVES_SAME_DAY") or 5)

    leaves_starting_on_day = sum(1 for r in active_leaves if r.get("start_date") == request_start_date.isoformat() and r.get("leave_type") != "BYPASS")
    if leaves_starting_on_day >= max_same_day:
        return (False, f"ไม่อนุมัติ: โควต้า 'วันเริ่มลา' {request_start_date.strftime('%d/%m/%Y')} เต็มแล้ว ({leaves_starting_on_day}/{max_same_day})")

    # ตรวจสอบโควต้าคนลาพร้อมกันสำหรับ L_7
    if leave_type == "L_7":
        max_overlap = int(config.get("MAX_OVERLAP_L7", 10))
        max_overlap_days = int(config.get("MAX_OVERLAP_DAYS_L7", 1))

        l7_records = [r for r in active_leaves if r.get("leave_type") == "L_7"]
        days_full_count = 0
        curr_date = request_start_date
        while curr_date <= request_end_date:
            overlap = 0
            for r in l7_records:
                try:
                    r_start = date.fromisoformat(r["start_date"])
                    r_end = date.fromisoformat(r["end_date"])
                    if r_start <= curr_date <= r_end:
                        overlap += 1
                except Exception:
                    continue
            if overlap >= max_overlap:
                days_full_count += 1
            curr_date += timedelta(days=1)

        if days_full_count > max_overlap_days:
            next_available = find_next_available_l7_slot(all_records, request_start_date + timedelta(days=1))
            err_msg = f"ไม่อนุมัติ: เนื่องจากช่วงเวลาที่ขอมีคนลาพร้อมกัน >= {max_overlap} คน อยู่ {days_full_count} วัน (อนุญาตไม่เกิน {max_overlap_days} วัน)"
            if next_available:
                err_msg += f"\n**ข้อเสนอแนะ:** วันที่เร็วที่สุดที่ว่าง คือเริ่มตั้งแต่วันที่ **{next_available.strftime('%d/%m/%Y')}**"
            return (False, err_msg)

    # อนุมัติบันทึกคำขอ
    new_record = {
        "record_id": f"L{len(all_records) + 1001}",
        "user_id": user_id,
        "leave_type": leave_type,
        "start_date": request_start_date.isoformat(),
        "end_date": request_end_date.isoformat(),
        "status": "ON_LEAVE"
    }
    all_data["leave_records"].append(new_record)

    if leave_type == "L_7":
        return_date = request_end_date + timedelta(days=1)
        new_next_l7 = get_next_l7_available_date(return_date)
        if user_id not in all_data["user_data"]:
            all_data["user_data"][user_id] = {}
        all_data["user_data"][user_id]["next_l7_available_date"] = new_next_l7.isoformat()

    if not await write_leave_db(all_data):
        return (False, "ระบบขัดข้อง: บันทึกข้อมูลล้มเหลว")

    return (True, f"อนุมัติสำเร็จ! (สถานะ: ON_LEAVE / รหัสใบลา: `{new_record['record_id']}`)")

async def cancel_leave_request(user_id_requesting: str, user_id_to_cancel: str, is_admin: bool, allow_active: bool = False) -> Tuple[bool, str]:
    all_data = await read_leave_db()
    all_records = all_data.get("leave_records", [])
    user_data = all_data.get("user_data", {})

    record_to_cancel = find_active_record(all_records, user_id_to_cancel)
    if record_to_cancel is None:
        return (False, f"ไม่พบข้อมูลการลา (ON_LEAVE หรือ รอกลับ) ของ User: <@{user_id_to_cancel}>")

    if not is_admin and user_id_requesting != user_id_to_cancel:
        return (False, "คุณไม่มีสิทธิ์ยกเลิกการลาของผู้อื่น (ต้องเป็น Admin)")

    today = datetime.now(config.timezone).date()
    try:
        start_date_obj = date.fromisoformat(record_to_cancel["start_date"])
        if start_date_obj.year > 3000:
            start_date_obj = start_date_obj.replace(year=start_date_obj.year - 543 * 2)
        elif start_date_obj.year >= 2400:
            start_date_obj = start_date_obj.replace(year=start_date_obj.year - 543)
    except Exception:
        start_date_obj = today

    if not is_admin and not allow_active and start_date_obj <= today and record_to_cancel.get("status") == "ON_LEAVE":
        return (False, f"ไม่สามารถยกเลิกได้! ใบลา `{record_to_cancel.get('record_id')}` ได้เริ่มขึ้นแล้ว (Admin เท่านั้นที่ยกเลิกได้)")

    record_to_cancel["status"] = "CANCELLED"
    record_to_cancel["cancelled_by"] = user_id_requesting
    record_to_cancel["cancelled_timestamp"] = datetime.now(config.timezone).isoformat()

    if record_to_cancel.get("leave_type") == "L_7":
        if user_id_to_cancel in user_data:
            user_data[user_id_to_cancel].pop("next_l7_available_date", None)

    if not await write_leave_db(all_data):
        return (False, "ระบบขัดข้อง: บันทึกการยกเลิกล้มเหลว")

    rec_id_str = f" (ID: `{record_to_cancel.get('record_id')}`)" if record_to_cancel.get('record_id') else ""
    return (True, f"ยกเลิกการลา{rec_id_str} ของ <@{user_id_to_cancel}> สำเร็จ!")

async def process_bypass_leave(admin_id: str, user_id: str, request_start_date: date, request_end_date: date) -> Tuple[bool, str]:
    all_data = await read_leave_db()
    all_records = all_data.get("leave_records", [])

    dur_days = (request_end_date - request_start_date).days + 1
    new_record = {
        "record_id": f"L-BYPASS-{len(all_records) + 1001}",
        "user_id": str(user_id),
        "leave_type": "BYPASS",
        "start_date": request_start_date.isoformat(),
        "end_date": request_end_date.isoformat(),
        "duration_days": dur_days,
        "reason": "Admin Force Leave (Bypass)",
        "status": "ON_LEAVE",
        "bypass_by_admin": str(admin_id)
    }
    all_data["leave_records"].append(new_record)

    if not await write_leave_db(all_data):
        return (False, "ระบบขัดข้อง: บันทึก Bypass ล้มเหลว")

    return (True, f"อนุมัติ (BYPASS) สำเร็จ! (สถานะ: ON_LEAVE / รหัสใบลา: `{new_record['record_id']}`)")

async def process_return_to_duty(user_id: str, confirming_officer: str) -> Tuple[bool, str]:
    all_data = await read_leave_db()
    all_records = all_data.get("leave_records", [])
    today_str = datetime.now(config.timezone).date().isoformat()

    record_to_complete = None
    for r in all_records:
        if r.get("user_id") == user_id:
            if r.get("status") == "EXPIRED_PENDING_RETURN":
                record_to_complete = r
                break
            if r.get("status") == "ON_LEAVE":
                start_date = r.get("start_date", "9999-12-31")
                if start_date <= today_str:
                    record_to_complete = r
                    break

    if record_to_complete is None:
        return (False, "ไม่พบข้อมูลการลาที่ 'กำลังลาอยู่' หรือ 'หมดอายุ' ของคุณให้กลับเข้าเวร")

    record_to_complete["status"] = "COMPLETED"
    record_to_complete["return_timestamp"] = datetime.now(config.timezone).isoformat()
    record_to_complete["confirming_officer"] = confirming_officer

    if not await write_leave_db(all_data):
        return (False, "ระบบขัดข้อง: บันทึก COMPLETED ล้มเหลว")

    return (True, f"บันทึกการกลับเข้าเวร (COMPLETED) สำเร็จ! (ผู้รับเข้าเวร: {confirming_officer})")

async def check_and_expire_leaves() -> List[Dict[str, str]]:
    all_data = await read_leave_db()
    all_records = all_data.get("leave_records", [])
    today_str = datetime.now(config.timezone).date().isoformat()
    users_to_notify = []
    needs_write = False

    for record in all_records:
        if record.get("status") == "ON_LEAVE":
            end_date_str = record.get("end_date", "9999-12-31")
            if end_date_str < today_str:
                record["status"] = "EXPIRED_PENDING_RETURN"
                users_to_notify.append({
                    "user_id": record.get("user_id"),
                    "end_date": end_date_str
                })
                needs_write = True

    if needs_write:
        await write_leave_db(all_data)

    return users_to_notify
