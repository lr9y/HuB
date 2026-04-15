# LRY Hub Bot (Python + Slash Commands)

بوت ديسكورد متكامل مبني بـ **Python (discord.py)** ويستخدم **Slash Commands** (`/`) بدل أوامر البادئة.

## التشغيل على Termux

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
# عدل القيم داخل .env
python bot.py
```

## الميزات الرئيسية
- Verification بزر Verify + مهلة 10 دقائق.
- Anti-Link / Anti-Spam / Badwords.
- Level System مع /top و /profile.
- Review + Suggestion embed forwarding.
- Admin slash commands (kick/ban/timeout/warn/clear/lock...)
- Logging لقنوات متعددة.

## ملاحظة
- تأكد من تفعيل **MESSAGE CONTENT INTENT** من Discord Developer Portal.
