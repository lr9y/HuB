# LRY Hub Bot (Python + Slash Commands)

بوت ديسكورد احترافي يعمل بالكامل بأوامر **Slash** فقط (`/`) وبدون أوامر `!`.

## التشغيل على Termux

```bash
pkg update -y && pkg upgrade -y
pkg install -y python git
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

## Elite Addons المضافة
- Save Config System داخل DB (`config` table).
- Restart Safe + Persistent State (SQLite).
- Auto Backup كل 12 ساعة + `/restore`.
- Channel Sync (`/sync_add`, `/sync_remove`).
- TTL Cache للبروفايل.
- Debug/Error monitor + Admin alerts.
- Advanced Log بدون تكرار عبر `event_id`.
- Load Protection (تعليق الأوامر الثقيلة وقت الضغط).
- Performance monitor (CPU/RAM).
- Welcome DM بعد التحقق.
- Slash aliases: `/t`, `/p`.
- XP freeze + remove xp.
- Security إضافي: new account detector, short link/invite/caps/emoji/cross-spam/name-change limits.
- Confirm ban بزر (`/ban_confirm`).
- Blacklist system لمستخدمي البوت.

## ملاحظة
تأكد من تفعيل Message Content Intent في Developer Portal.
