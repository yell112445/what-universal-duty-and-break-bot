import os
import json
import asyncio
from zoneinfo import ZoneInfo
from typing import Any, Optional
from dotenv import load_dotenv

# โหลด .env
load_dotenv()

CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "data", "config.json")

class ConfigManager:
    _instance: Optional['ConfigManager'] = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._config = {}
            cls._instance._load_sync()
        return cls._instance

    def _load_sync(self):
        """โหลดไฟล์ config.json แบบซิงโครนัสตอนสตาร์ตอัป (สร้างใหม่จาก template ถ้ายังไม่มี)"""
        if not os.path.exists(CONFIG_FILE_PATH):
            example_path = os.path.join(os.path.dirname(__file__), "data", "config.example.json")
            if os.path.exists(example_path):
                try:
                    import shutil
                    os.makedirs(os.path.dirname(CONFIG_FILE_PATH), exist_ok=True)
                    shutil.copyfile(example_path, CONFIG_FILE_PATH)
                except Exception as e:
                    print(f"[ConfigManager] Could not copy config.example.json: {e}")

        if os.path.exists(CONFIG_FILE_PATH):
            try:
                with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
            except Exception as e:
                print(f"[ConfigManager] Error reading {CONFIG_FILE_PATH}: {e}")
                self._config = {}
        else:
            self._config = {}

    async def reload(self) -> bool:
        """โหลดค่าจากไฟล์ config.json ใหม่เข้า Memory (Hot Reload)"""
        async with self._lock:
            try:
                def _read():
                    with open(CONFIG_FILE_PATH, "r", encoding="utf-8") as f:
                        return json.load(f)
                self._config = await asyncio.to_thread(_read)
                print("[ConfigManager] Config reloaded successfully.")
                return True
            except Exception as e:
                print(f"[ConfigManager] Failed to reload config: {e}")
                return False

    async def save(self) -> bool:
        """บันทึกค่าใน Memory ลงไฟล์ config.json"""
        async with self._lock:
            try:
                def _write():
                    with open(CONFIG_FILE_PATH, "w", encoding="utf-8") as f:
                        json.dump(self._config, f, indent=2, ensure_ascii=False)
                await asyncio.to_thread(_write)
                return True
            except Exception as e:
                print(f"[ConfigManager] Failed to save config: {e}")
                return False

    def get(self, key: str, default: Any = None) -> Any:
        """ดึงค่าจาก Config ตาม Key"""
        return self._config.get(key, default)

    async def set(self, key: str, value: Any) -> bool:
        """อัปเดตค่า Config และบันทึกลงไฟล์ทันทีโดยไม่ต้องรีสตาร์ตบอท"""
        self._config[key] = value
        return await self.save()

    # --- Property Helpers & Environment Secrets ---
    @property
    def token(self) -> str:
        # Token อ่านจาก Environment หรือ .env เสมอ
        return os.getenv("DISCORD_TOKEN", "")

    @property
    def heartbeat_webhook_url(self) -> str:
        return os.getenv("HEARTBEAT_WEBHOOK_URL", "")

    @property
    def timezone(self) -> ZoneInfo:
        tz_name = self.get("TIMEZONE", "Asia/Bangkok")
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("Asia/Bangkok")

    @property
    def guild_id(self) -> int:
        return int(self.get("GUILD_ID", 1334148289330806848))

    @property
    def super_assistants(self) -> list[int]:
        raw = self.get("SUPER_ASSISTANT", [])
        return [int(x) for x in raw] if isinstance(raw, list) else [int(raw)]

    @property
    def assistants(self) -> list[int]:
        raw = self.get("ASSISTANT", [])
        return [int(x) for x in raw] if isinstance(raw, list) else [int(raw)]

    @property
    def admin_ids(self) -> list[int]:
        raw = self.get("ADMIN_IDS", [])
        return [int(x) for x in raw] if isinstance(raw, list) else [int(raw)]

    @property
    def owner_id(self) -> int:
        return int(self.get("OWNER_ID", 707212479490424833))

    @property
    def role_mapping(self) -> dict[int, str]:
        raw = self.get("ROLE_MAPPING", {})
        return {int(k): v for k, v in raw.items()}

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids or user_id == self.owner_id or user_id in self.super_assistants

    def is_assistant_or_above(self, user_id: int) -> bool:
        return self.is_admin(user_id) or user_id in self.assistants

config = ConfigManager()
