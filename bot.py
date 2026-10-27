import sys
import asyncio
from main import main, logger

# จุดเริ่มต้นสำหรับโฮสต์ที่เรียกหา bot.py
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("บอทถูกหยุดการทำงานโดยผู้ใช้ (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"เกิดข้อผิดพลาดร้ายแรง: {e}", exc_info=True)
