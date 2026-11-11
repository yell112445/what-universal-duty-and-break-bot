import os
import logging
from typing import Optional, Dict, Any
import aiohttp
from aiohttp import web
try:
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
except ImportError:
    import base64
    import hmac
    import hashlib
    import time
    import json

    class BadSignature(Exception):
        pass

    class SignatureExpired(BadSignature):
        pass

    class URLSafeTimedSerializer:
        def __init__(self, secret_key: str):
            self.secret_key = secret_key.encode("utf-8")

        def dumps(self, obj: Any) -> str:
            raw_json = json.dumps(obj, separators=(',', ':')).encode("utf-8")
            b64_payload = base64.urlsafe_b64encode(raw_json).decode("utf-8").rstrip("=")
            ts = str(int(time.time()))
            msg = f"{b64_payload}.{ts}".encode("utf-8")
            sig = base64.urlsafe_b64encode(hmac.new(self.secret_key, msg, hashlib.sha256).digest()).decode("utf-8").rstrip("=")
            return f"{b64_payload}.{ts}.{sig}"

        def loads(self, token: str, max_age: int = 604800) -> Any:
            parts = token.split(".")
            if len(parts) != 3:
                raise BadSignature("Invalid token structure")
            b64_payload, ts_str, sig = parts
            try:
                ts = int(ts_str)
            except ValueError:
                raise BadSignature("Invalid timestamp")

            if time.time() - ts > max_age:
                raise SignatureExpired("Token expired")

            msg = f"{b64_payload}.{ts_str}".encode("utf-8")
            expected_sig = base64.urlsafe_b64encode(hmac.new(self.secret_key, msg, hashlib.sha256).digest()).decode("utf-8").rstrip("=")
            if not hmac.compare_digest(sig, expected_sig):
                raise BadSignature("Signature mismatch")

            pad_len = 4 - (len(b64_payload) % 4)
            if pad_len < 4:
                b64_payload += "=" * pad_len
            raw_json = base64.urlsafe_b64decode(b64_payload.encode("utf-8"))
            return json.loads(raw_json)

from config_manager import config

logger = logging.getLogger("DashboardAuth")

# ชื่อ Session Cookie และกุญแจเข้ารหัส
SESSION_COOKIE_NAME = "what_session"
SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY") or config.get("DASHBOARD_SECRET_KEY") or "what-universal-secret-key-v1"
serializer = URLSafeTimedSerializer(SECRET_KEY)

DISCORD_API_ENDPOINT = "https://discord.com/api/v10"

def get_redirect_uri(request: web.Request) -> str:
    """คำนวณ Redirect URI อัตโนมัติจาก Request หรือดึงจาก Config"""
    custom_url = config.get("DASHBOARD_URL")
    if custom_url:
        return f"{custom_url.rstrip('/')}/auth/callback"
    host = request.headers.get("Host") or f"127.0.0.1:{request.app['port']}"
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme
    return f"{scheme}://{host}/auth/callback"

def get_oauth2_url(request: web.Request, client_id: str) -> str:
    """สร้าง URL สำหรับส่งผู้ใช้ไปกดยืนยันตัวตนบน Discord OAuth2"""
    redirect_uri = get_redirect_uri(request)
    scope = "identify guilds.members.read"
    import urllib.parse
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "prompt": "consent"
    }
    return f"https://discord.com/oauth2/authorize?{urllib.parse.urlencode(params)}"

def create_session_token(user_data: Dict[str, Any]) -> str:
    """สร้าง Token เข้ารหัสสำหรับเก็บลงใน Session Cookie (หมดอายุใน 7 วัน)"""
    return serializer.dumps(user_data)

def verify_session_token(token: str, max_age: int = 604800) -> Optional[Dict[str, Any]]:
    """ถอดรหัสและตรวจสอบความถูกต้องของ Session Token"""
    try:
        data = serializer.loads(token, max_age=max_age)
        return data
    except (BadSignature, SignatureExpired):
        return None

def get_session_user(request: web.Request) -> Optional[Dict[str, Any]]:
    """อ่านข้อมูลผู้ใช้ที่ล็อกอินอยู่จาก Session Cookie"""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)

async def check_user_permission(bot, user_id: int) -> tuple[bool, str]:
    """
    ตรวจสอบว่าผู้ใช้มีสิทธิ์ระดับ Admin หรือไม่
    คืนค่าเป็น (is_allowed: bool, role_title: str)
    """
    if config.is_owner(user_id):
        return True, "Server Owner"
    if config.is_admin(user_id):
        return True, "Administrator"

    guild = bot.get_guild(config.guild_id)
    if guild:
        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None

        if member:
            if member.guild_permissions.administrator:
                return True, "Guild Admin"
            # ตรวจสอบว่ามี role ที่ได้รับอนุญาต
            super_assistants = config.super_assistants
            for rid in super_assistants:
                if member.get_role(rid):
                    return True, "Super Assistant"
            assistants = config.assistants
            for rid in assistants:
                if member.get_role(rid):
                    return True, "Assistant"

    return False, "User"
