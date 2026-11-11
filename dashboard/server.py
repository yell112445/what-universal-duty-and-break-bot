import os
import asyncio
import logging
from typing import Optional
from aiohttp import web

from dashboard.routes_api import routes as api_routes
from dashboard.routes_views import view_routes

logger = logging.getLogger("DashboardServer")

_runner: Optional[web.AppRunner] = None
_site: Optional[web.TCPSite] = None

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

async def create_app(bot, port: int) -> web.Application:
    """สร้างและกำหนดค่า Application สำหรับ Dashboard Web Server"""
    app = web.Application()
    app["bot"] = bot
    app["port"] = port

    # ติดตั้ง Static Files Directory
    os.makedirs(STATIC_DIR, exist_ok=True)
    os.makedirs(os.path.join(STATIC_DIR, "css"), exist_ok=True)
    os.makedirs(os.path.join(STATIC_DIR, "js"), exist_ok=True)
    app.router.add_static("/static", STATIC_DIR)

    # ลงทะเบียน Routes ทั้งหมด
    app.add_routes(view_routes)
    app.add_routes(api_routes)

    return app

async def start_dashboard(bot, host: str = "0.0.0.0", port: int = 8080):
    """สตาร์ต Web Dashboard Server แบบ Asynchronous บน Event Loop เดียวกับ Discord Bot"""
    global _runner, _site
    if _runner is not None:
        logger.warning("Dashboard Server ทำงานอยู่แล้ว")
        return

    try:
        app = await create_app(bot, port)
        _runner = web.AppRunner(app)
        await _runner.setup()
        _site = web.TCPSite(_runner, host=host, port=port)
        await _site.start()
        logger.info(f"🌐 Web Dashboard ทำงานแล้วที่ http://{host}:{port}")
    except Exception as e:
        logger.error(f"❌ ไม่สามารถเริ่ม Dashboard Server บนพอร์ต {port}: {e}", exc_info=True)
        if _runner:
            await _runner.cleanup()
            _runner = None
            _site = None

async def stop_dashboard():
    """หยุดการทำงานของ Web Dashboard Server อย่างปลอดภัย"""
    global _runner, _site
    if _runner:
        try:
            logger.info("กำลังปิด Dashboard Server...")
            await _runner.cleanup()
        except Exception as e:
            logger.error(f"Error during dashboard server cleanup: {e}")
        finally:
            _runner = None
            _site = None
