import os
import json
import asyncio
from typing import Any, Dict, Optional
import re
from datetime import date, datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
OPERATIONS_DIR = os.path.join(DATA_DIR, "operations")
AUTHORIZED_USERS_DIR = os.path.join(DATA_DIR, "authorized_users")

# Lock แยกตามหมวดหมู่เพื่อป้องกัน blocking ข้ามกัน
_duties_lock = asyncio.Lock()
_op_status_lock = asyncio.Lock()
_op_data_lock = asyncio.Lock()
_db_lock = asyncio.Lock()

async def read_json_file(file_path: str, default: Any = None) -> Any:
    """อ่านไฟล์ JSON แบบ Asynchronous ไม่บล็อก Event Loop"""
    def _read():
        if not os.path.exists(file_path):
            return default
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return default
        except Exception as e:
            print(f"[StorageService] Error reading {file_path}: {e}")
            return default
    return await asyncio.to_thread(_read)

async def write_json_file(file_path: str, data: Any) -> bool:
    """เขียนไฟล์ JSON แบบ Asynchronous ไม่บล็อก Event Loop"""
    def _write():
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[StorageService] Error writing {file_path}: {e}")
            return False
    return await asyncio.to_thread(_write)

# --- 1. Duty Tracker Storage ---
CURRENT_DUTIES_FILE = os.path.join(DATA_DIR, "current_duties.json")
PENDING_ALERTS_FILE = os.path.join(DATA_DIR, "pending_alerts.json")
LAST_MSG_FILE = os.path.join(DATA_DIR, "last_message_id.json")

async def load_current_duties() -> Dict[str, Any]:
    async with _duties_lock:
        return await read_json_file(CURRENT_DUTIES_FILE, {}) or {}

async def save_current_duties(data: Dict[str, Any]) -> bool:
    async with _duties_lock:
        return await write_json_file(CURRENT_DUTIES_FILE, data)

async def load_pending_alerts() -> Dict[str, Any]:
    async with _duties_lock:
        return await read_json_file(PENDING_ALERTS_FILE, {}) or {}

async def save_pending_alerts(data: Dict[str, Any]) -> bool:
    async with _duties_lock:
        return await write_json_file(PENDING_ALERTS_FILE, data)

async def load_last_message_id() -> int:
    async with _duties_lock:
        data = await read_json_file(LAST_MSG_FILE, 0)
        try:
            return int(data)
        except (ValueError, TypeError):
            return 0

async def save_last_message_id(msg_id: int) -> bool:
    async with _duties_lock:
        return await write_json_file(LAST_MSG_FILE, int(msg_id))

# --- 2. Operations Storage ---
OPERATION_STATUS_FILE = os.path.join(DATA_DIR, "operation_status.json")

async def load_operation_status() -> Dict[str, Any]:
    async with _op_status_lock:
        return await read_json_file(OPERATION_STATUS_FILE, {}) or {}

async def save_operation_status(data: Dict[str, Any]) -> bool:
    async with _op_status_lock:
        return await write_json_file(OPERATION_STATUS_FILE, data)

def get_operation_daily_filepath(date_obj) -> str:
    y = date_obj.strftime("%Y")
    m = date_obj.strftime("%m")
    d = date_obj.strftime("%d")
    return os.path.join(OPERATIONS_DIR, y, m, f"{d}.json")

async def load_operation_daily_data(date_obj) -> Dict[str, Any]:
    path = get_operation_daily_filepath(date_obj)
    async with _op_data_lock:
        return await read_json_file(path, {}) or {}

async def save_operation_session_data(user_id: str, player_name: str, start: str, end: str, duration: float, source: str = "manual"):
    try:
        dt = datetime.fromisoformat(start)
    except Exception:
        dt = datetime.now()
    path = get_operation_daily_filepath(dt)

    async with _op_data_lock:
        daily_data = await read_json_file(path, {}) or {}
        if user_id not in daily_data:
            daily_data[user_id] = {
                "player_name": player_name,
                "sessions": []
            }
        daily_data[user_id]["player_name"] = player_name
        daily_data[user_id]["sessions"].append({
            "start": start,
            "end": end,
            "duration_seconds": duration,
            "source": source
        })
        await write_json_file(path, daily_data)

# --- 3. Breaktime Storage ---
DB_FILE = os.path.join(DATA_DIR, "database.json")

async def read_leave_db() -> Dict[str, Any]:
    async with _db_lock:
        data = await read_json_file(DB_FILE, None)
        if data is None or not isinstance(data, dict):
            data = {
                "leave_board_message_id": None,
                "leave_panel_message_id": None,
                "leave_records": [],
                "user_data": {}
            }
        data.setdefault("leave_records", [])
        data.setdefault("user_data", {})
        data.setdefault("leave_board_message_id", None)
        data.setdefault("leave_panel_message_id", None)
        # ซ่อมแซมประวัติเดิมที่บันทึกปี พ.ศ. หรือ ค.ศ. เกินให้อัตโนมัติ
        for r in data.get("leave_records", []):
            if "start_date" in r:
                r["start_date"] = sanitize_iso_date(r["start_date"])
            if "end_date" in r:
                r["end_date"] = sanitize_iso_date(r["end_date"])
        return data

async def write_leave_db(data: Dict[str, Any]) -> bool:
    async with _db_lock:
        return await write_json_file(DB_FILE, data)

# --- 4. Welcome Ticket / Introduction Storage ---
WELCOME_TICKETS_FILE = os.path.join(DATA_DIR, "welcome_tickets.json")
_welcome_lock = asyncio.Lock()

async def load_welcome_tickets() -> Dict[str, Any]:
    async with _welcome_lock:
        data = await read_json_file(WELCOME_TICKETS_FILE, None)
        if data is None or not isinstance(data, dict):
            data = {"_system": {"enabled": True}, "tickets": {}}
        data.setdefault("_system", {"enabled": True})
        data.setdefault("tickets", {})
        return data

async def save_welcome_tickets(data: Dict[str, Any]) -> bool:
    async with _welcome_lock:
        return await write_json_file(WELCOME_TICKETS_FILE, data)



# --- Smart Date Parser & Year Sanitizer (พ.ศ. / ค.ศ. Full Support) ---
THAI_MONTHS = {
    "มกราคม": 1, "ม.ค.": 1, "มค": 1, "มกรา": 1, "jan": 1, "january": 1,
    "กุมภาพันธ์": 2, "ก.พ.": 2, "กพ": 2, "กุมภา": 2, "feb": 2, "february": 2,
    "มีนาคม": 3, "มี.ค.": 3, "มีค": 3, "มีนา": 3, "mar": 3, "march": 3,
    "เมษายน": 4, "เม.ย.": 4, "เมย": 4, "เมษา": 4, "apr": 4, "april": 4,
    "พฤษภาคม": 5, "พ.ค.": 5, "พค": 5, "พฤษภา": 5, "may": 5,
    "มิถุนายน": 6, "มิ.ย.": 6, "มิย": 6, "มิถุนา": 6, "jun": 6, "june": 6,
    "กรกฎาคม": 7, "ก.ค.": 7, "กค": 7, "กรกฎา": 7, "jul": 7, "july": 7,
    "สิงหาคม": 8, "ส.ค.": 8, "สค": 8, "สิงหา": 8, "aug": 8, "august": 8,
    "กันยายน": 9, "ก.ย.": 9, "กย": 9, "กันยา": 9, "sep": 9, "september": 9,
    "ตุลาคม": 10, "ต.ค.": 10, "ตค": 10, "ตุลา": 10, "oct": 10, "october": 10,
    "พฤศจิกายน": 11, "พ.ย.": 11, "พย": 11, "พฤศจิกา": 11, "nov": 11, "november": 11,
    "ธันวาคม": 12, "ธ.ค.": 12, "ธค": 12, "ธันวา": 12, "dec": 12, "december": 12
}

def parse_smart_date(date_str: str, tz=None) -> Optional[date]:
    """แปลงข้อความวันที่เป็น date object (ค.ศ. เสมอ) อย่างชาญฉลาด
    รองรับทั้ง พ.ศ., ค.ศ., ตัวย่อ 2 หลัก, วันนี้, พรุ่งนี้, ชื่อเดือนไทย/อังกฤษ
    """
    if not date_str or not isinstance(date_str, str):
        return None

    raw = date_str.strip().lower()
    from zoneinfo import ZoneInfo
    target_tz = tz or ZoneInfo("Asia/Bangkok")
    now_dt = datetime.now(target_tz)
    today = now_dt.date()

    if raw in ["วันนี้", "today", "now"]:
        return today
    if raw in ["พรุ่งนี้", "tomorrow", "tmr"]:
        return today + timedelta(days=1)
    if raw in ["มะรืน", "มะรืนนี้", "day after tomorrow"]:
        return today + timedelta(days=2)

    # ยุบจุดในตัวย่อเดือนไทย เช่น ต.ค. -> ตค
    raw = re.sub(r'(?<=[ก-ฮ])\.(?=[ก-ฮ]|\s|$)', '', raw)

    # แยกคำหรือตัวเลข
    clean = re.sub(r'[\/\-\.\s,]+', ' ', raw).strip()
    tokens = clean.split()

    day = None
    month = None
    year = None

    if len(tokens) == 3:
        d_part, m_part, y_part = tokens
        try:
            day = int(d_part)
        except ValueError:
            return None

        if m_part in THAI_MONTHS:
            month = THAI_MONTHS[m_part]
        elif m_part.isdigit():
            month = int(m_part)
        else:
            return None

        try:
            year = int(y_part)
        except ValueError:
            return None

    elif len(tokens) == 2:
        d_part, m_part = tokens
        try:
            day = int(d_part)
        except ValueError:
            return None

        if m_part in THAI_MONTHS:
            month = THAI_MONTHS[m_part]
        elif m_part.isdigit():
            month = int(m_part)
        else:
            return None

        year = today.year
    else:
        return None

    # ปรับปี ค.ศ. / พ.ศ. ให้เป็น ค.ศ. ถูกต้องเสมอ
    if year >= 2400:
        # พ.ศ. ชัดเจน เช่น 2569 -> 2026
        year = year - 543
    elif 40 <= year <= 99:
        # พ.ศ. 2 หลัก เช่น 69 -> 2569 -> 2026
        year = (2500 + year) - 543
    elif 0 <= year < 40:
        # ค.ศ. 2 หลัก เช่น 26 -> 2026
        year = 2000 + year

    try:
        parsed_d = date(year, month, day)
        if len(tokens) == 2 and parsed_d < today:
            parsed_d = date(year + 1, month, day)
        return parsed_d
    except ValueError:
        return None

def sanitize_iso_date(d_str: str) -> str:
    """ซ่อมแซมและปรับปี ค.ศ. / พ.ศ. ให้ถูกต้องเสมอ เช่น หากถูกบันทึกเป็น 2569 หรือ 3112 ให้ปรับเป็น ค.ศ. 2026"""
    if not d_str or not isinstance(d_str, str):
        return d_str
    try:
        parts = d_str.split("-")
        if len(parts) == 3:
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            if y > 3000:
                y -= 543 * 2
            elif y >= 2400:
                y -= 543
            return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        pass
    return d_str
