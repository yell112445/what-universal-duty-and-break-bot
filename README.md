# What Universal - Duty & Breaktime Bot

บอทระบบปฏิบัติการ (Operations), ตรวจจับเวลาเข้า-ออกเวร (Duty Tracker), และระบบลางาน (Breaktime) สำหรับเซิร์ฟเวอร์ What Universal
พัฒนาขึ้นใหม่โดยรวมฟังก์ชันจาก `dutycheck69` และ `breaktime69` เข้าด้วยกัน พร้อมสถาปัตยกรรมแบบ Modular (Cogs) และระบบ Hot Config แบบ Zero-Downtime

---

## 📁 โครงสร้างโปรเจกต์

```text
what_universal_duty_and_break_bot/
├── .env.example              # ตัวอย่าง Environment Variables
├── .env                      # ใส่ DISCORD_TOKEN และ Secrets
├── requirements.txt          # Python dependencies
├── main.py                   # จุดสตาร์ตอัปของบอท (สำหรับ Pterodactyl Panel)
├── config_manager.py         # ตัวจัดการการตั้งค่าแบบ Asynchronous Hot Reload
├── cogs/
│   ├── operations.py         # ระบบปฏิบัติการ, OTP, แผงลงเวลา, Export Excel, Backup
│   ├── breaktime.py          # ระบบลางาน, Leave Board, Leave Panel, Cooldown
│   ├── duty_check.py         # ระบบดักจับ Log เข้า-ออกเวร (ปิดชั่วคราว พร้อมเปิด)
│   └── config_cog.py         # คำสั่ง /config ปรับแต่งค่าแบบ Realtime ไม่ต้องรีสตาร์ต
├── services/
│   ├── storage_service.py    # Non-blocking Async JSON File Handler
│   └── leave_service.py      # Business Logic กฎและโควต้าการลางาน
└── data/
    ├── config.json           # การตั้งค่าระบบทั้งหมด (แก้ไขสดได้)
    ├── database.json         # ฐานข้อมูลการลางาน
    ├── current_duties.json   # บันทึกสถานะผู้เล่น On-duty / Off-duty
    ├── pending_alerts.json   # รายการแจ้งเตือนที่รอดำเนินการ
    ├── operation_status.json # สถานะรอบการทำงานปัจจุบัน
    └── authorized_users/     # โฟลเดอร์เก็บรายชื่อผู้มีสิทธิ์ลางาน (.txt รายเดือน)
```

---

## 🚀 วิธีนำขึ้นรันบน Dashboard ของ bot-hosting.net (Pterodactyl Panel)

เนื่องจากบนหน้าเว็บ Dashboard ของ bot-hosting.net เป็นระบบ Container ควบคุมผ่านหน้าเว็บ (ไม่มีหน้าจอ Terminal เหมือนบนคอมพิวเตอร์) การตั้งค่าจึงทำผ่าน UI หน้าเว็บทั้งหมดดังนี้:

### 1. อัปโหลดไฟล์ (แท็บ File Manager / Files)
1. บีบอัดไฟล์ทั้งหมดในโฟลเดอร์นี้เป็นไฟล์ `.zip`
2. ไปที่แท็บ **Files** บน Dashboard -> กดปุ่ม **Upload** แล้วเลือกไฟล์ zip
3. เมื่ออัปโหลดเสร็จ ให้คลิกที่จุดสามจุด (`...`) ท้ายไฟล์ zip แล้วกด **Unarchive** (แตกไฟล์)
4. คลิกที่ไฟล์ `.env` บนหน้าเว็บ ใส่ Token ของบอท:
   ```env
   DISCORD_TOKEN=โทเคนบอทของคุณ
   ```
   แล้วกด **Save Content**

### 2. ตั้งค่าไฟล์เริ่มทำงาน (แท็บ Startup)
1. ไปที่แท็บ **Startup** บนเมนูด้านซ้าย
2. ตรวจสอบช่อง **Start-up File** หรือ **Main File** ให้ระบุเป็น:
   ```text
   main.py
   ```
   *(หมายเหตุ: ระบบของ bot-hosting.net จะอ่านไฟล์ `requirements.txt` ที่อยู่ในหน้า Files และติดตั้งแพ็กเกจให้โดยอัตโนมัติทุกครั้งที่กดเริ่มทำงาน ไม่ต้องพิมพ์คำสั่ง pip เอง)*

### 3. เริ่มต้นทำงานและดู Log (แท็บ Console)
1. ไปที่แท็บ **Console**
2. กดปุ่ม **Start** (สีฟ้า/เขียว)
3. ระบบจะดาวน์โหลด dependencies และรัน `main.py` โดยคุณจะเห็น Log การทำงานของบอทปรากฏขึ้นบนหน้าจอดำทันที!

---

## ⚙️ คำสั่ง Slash Commands ที่พร้อมใช้งาน

### 🛡️ Operations (ระบบปฏิบัติการ)
- `/operation_start` — เริ่มรอบการทำงานใหม่ (สร้างปุ่มกดลงเวลา + ส่งรหัส OTP ไปห้อง OTP)
- `/operation_stop` — ยุติรอบการทำงาน สรุปเวลาทุกคน และลบปุ่ม/OTP
- `/force_stop [member]` — บังคับให้ออกเวรเป็นรายคน
- `/operation_get_daily_data [date] [min_hours]` — ดึงรายงานประจำวันและส่งออกเป็นไฟล์ Excel
- `/operation_backup` — สำรองข้อมูลโฟลเดอร์ data ทั้งหมดเป็นไฟล์ ZIP

### 🌴 Breaktime (ระบบลางาน)
- `/leave` — เรียกแผงขอลางาน (ลา 4/3 วัน, ลาพักร้อน 7 วัน, กลับเข้าเวร)
- `/cancel_leave [member]` — ขอยกเลิกการลา
- `/force_leave [member] [start_date] [end_date]` — [Admin] บังคับลาข้ามเงื่อนไข
- `/upload_auth_list [file]` — [Admin] อัปโหลดไฟล์รายชื่อผู้มีสิทธิ์ลางานประจำเดือน (`MM_YYYY.txt`)
- `/bot_info` — ดูสถานะและเวอร์ชันของบอท

### ⚙️ Config & Core (การตั้งค่า Realtime)
- `/config` — แผงตั้งค่าระบบแบบ Interactive (มีเมนู Dropdown + Modal ให้แก้ได้ทันที ไม่ต้องรีบอท)
- `/config_reload` — โหลดค่าจาก `data/config.json` เข้า Memory ใหม่ทันที
- `/config_set [key] [value]` — ตั้งค่าตัวแปรใน config โดยตรง
