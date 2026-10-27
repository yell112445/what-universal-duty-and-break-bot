import os
import json
import asyncio
from typing import Any, Dict, Optional

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
    from datetime import datetime
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
        return data

async def write_leave_db(data: Dict[str, Any]) -> bool:
    async with _db_lock:
        return await write_json_file(DB_FILE, data)
