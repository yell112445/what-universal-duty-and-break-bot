import os
import logging
import urllib.parse
from aiohttp import web
from jinja2 import Environment, FileSystemLoader

from config_manager import config
from dashboard.auth import (
    SESSION_COOKIE_NAME, get_session_user, create_session_token,
    get_oauth2_url, get_redirect_uri, check_user_permission, DISCORD_API_ENDPOINT
)

logger = logging.getLogger("DashboardViews")

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
jinja_env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True
)

view_routes = web.RouteTableDef()

def render_template(template_name: str, context: dict) -> web.Response:
    template = jinja_env.get_template(template_name)
    html_content = template.render(**context)
    return web.Response(text=html_content, content_type="text/html")

# --- Authentication Views ---
@view_routes.get("/login")
async def page_login(request: web.Request):
    user = get_session_user(request)
    if user:
        raise web.HTTPFound(location="/")

    bot = request.app["bot"]
    client_id = str(config.get("DISCORD_CLIENT_ID") or (bot.user.id if bot.user else ""))
    client_secret = config.get("DISCORD_CLIENT_SECRET", "")
    has_oauth2 = bool(client_id and client_secret)

    oauth_url = get_oauth2_url(request, client_id) if has_oauth2 else None
    error = request.query.get("error")

    return render_template("login.html", {
        "has_oauth2": has_oauth2,
        "oauth_url": oauth_url,
        "error": error
    })

@view_routes.get("/auth/login")
async def auth_login_redirect(request: web.Request):
    bot = request.app["bot"]
    client_id = str(config.get("DISCORD_CLIENT_ID") or (bot.user.id if bot.user else ""))
    client_secret = config.get("DISCORD_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise web.HTTPFound(location="/login?error=oauth_not_configured")

    url = get_oauth2_url(request, client_id)
    raise web.HTTPFound(location=url)

@view_routes.get("/auth/callback")
async def auth_callback(request: web.Request):
    code = request.query.get("code")
    if not code:
        raise web.HTTPFound(location="/login?error=no_code")

    bot = request.app["bot"]
    client_id = str(config.get("DISCORD_CLIENT_ID") or (bot.user.id if bot.user else ""))
    client_secret = config.get("DISCORD_CLIENT_SECRET", "")
    redirect_uri = get_redirect_uri(request)

    # แลก code เป็น Access Token
    import aiohttp
    async with aiohttp.ClientSession() as session:
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri
        }
        async with session.post(f"{DISCORD_API_ENDPOINT}/oauth2/token", data=token_data) as resp:
            if resp.status != 200:
                err_text = await resp.text()
                logger.error(f"OAuth2 token exchange failed: {err_text}")
                raise web.HTTPFound(location="/login?error=token_failed")
            token_json = await resp.json()

        access_token = token_json.get("access_token")
        headers = {"Authorization": f"Bearer {access_token}"}

        # ดึงข้อมูลโปรไฟล์ผู้ใช้
        async with session.get(f"{DISCORD_API_ENDPOINT}/users/@me", headers=headers) as resp:
            if resp.status != 200:
                raise web.HTTPFound(location="/login?error=profile_failed")
            user_info = await resp.json()

    user_id = int(user_info["id"])
    username = user_info.get("global_name") or user_info.get("username")
    avatar = f"https://cdn.discordapp.com/avatars/{user_id}/{user_info.get('avatar')}.png" if user_info.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png"

    # ตรวจสอบสิทธิ์ Admin
    is_allowed, role_title = await check_user_permission(bot, user_id)
    if not is_allowed:
        return render_template("login.html", {
            "has_oauth2": True,
            "error": f"ปฏิเสธการเข้าถึง: บัญชี {username} (ID: {user_id}) ไม่มีสิทธิ์ระดับแอดมินในระบบ"
        })

    # ออก Session Token
    session_data = {
        "user_id": str(user_id),
        "username": username,
        "avatar": avatar,
        "role_title": role_title,
        "auth_type": "oauth2"
    }
    cookie_token = create_session_token(session_data)

    response = web.HTTPFound(location="/")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_token,
        max_age=604800,  # 7 วัน
        httponly=True,
        samesite="Lax"
    )
    return response

@view_routes.post("/auth/login_passcode")
async def auth_login_passcode(request: web.Request):
    """Fallback: เข้าสู่ระบบด้วย Master Passcode"""
    data = await request.post()
    passcode = data.get("passcode", "").strip()

    valid_passcode = config.get("ADMIN_PASSCODE", "admin1234")
    if not passcode or passcode != valid_passcode:
        raise web.HTTPFound(location="/login?error=invalid_passcode")

    session_data = {
        "user_id": "0",
        "username": "Super Admin",
        "avatar": "https://cdn.discordapp.com/embed/avatars/1.png",
        "role_title": "Master Administrator",
        "auth_type": "passcode"
    }
    cookie_token = create_session_token(session_data)

    response = web.HTTPFound(location="/")
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie_token,
        max_age=604800,
        httponly=True,
        samesite="Lax"
    )
    return response

@view_routes.get("/logout")
async def auth_logout(request: web.Request):
    response = web.HTTPFound(location="/login")
    response.del_cookie(SESSION_COOKIE_NAME)
    return response

# --- Dashboard Pages ---
def get_common_context(request: web.Request, active_tab: str) -> dict:
    user = get_session_user(request)
    bot = request.app["bot"]
    return {
        "active_tab": active_tab,
        "user": user,
        "bot_name": str(bot.user) if bot.user else "What Universal Bot",
        "bot_avatar": str(bot.user.display_avatar.url) if bot.user else "",
        "version": config.get("BOT_VERSION", "v1.0.0")
    }

@view_routes.get("/")
async def page_root(request: web.Request):
    user = get_session_user(request)
    if not user:
        raise web.HTTPFound(location="/login")
    raise web.HTTPFound(location="/overview")

@view_routes.get("/overview")
async def page_overview(request: web.Request):
    user = get_session_user(request)
    if not user:
        raise web.HTTPFound(location="/login")
    ctx = get_common_context(request, "overview")
    return render_template("overview.html", ctx)

@view_routes.get("/operations")
async def page_operations(request: web.Request):
    user = get_session_user(request)
    if not user:
        raise web.HTTPFound(location="/login")
    ctx = get_common_context(request, "operations")
    return render_template("operations.html", ctx)

@view_routes.get("/leaves")
async def page_leaves(request: web.Request):
    user = get_session_user(request)
    if not user:
        raise web.HTTPFound(location="/login")
    ctx = get_common_context(request, "leaves")
    return render_template("leaves.html", ctx)

@view_routes.get("/duties")
async def page_duties(request: web.Request):
    user = get_session_user(request)
    if not user:
        raise web.HTTPFound(location="/login")
    ctx = get_common_context(request, "duties")
    return render_template("duties.html", ctx)

@view_routes.get("/settings")
async def page_settings(request: web.Request):
    user = get_session_user(request)
    if not user:
        raise web.HTTPFound(location="/login")
    ctx = get_common_context(request, "settings")
    return render_template("settings.html", ctx)
