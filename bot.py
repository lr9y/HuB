import json
import os
import re
import shutil
import sqlite3
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import discord
import psutil
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Defaults:
    guild_id: int = int(os.getenv("GUILD_ID", "1493967262477189310"))
    owner_id: int = int(os.getenv("OWNER_ID", "1493986702069465088"))

    verification_channel: int = 1493979392379060395
    verification_log: int = 1494017006377369762

    unverified_role: int = 1493991180847812869
    verified_role: int = 1493991102380900466
    member_role: int = 1493990746024448010

    chat_channel: int = 1493980621452873748
    level_channel: int = 1493980921383358565
    reviews_channel: int = 1493983841528840202
    suggestions_channel: int = 1494020158451224769
    support_suggestions_channel: int = 1494002025065742446

    automod_log: int = 1494003519257051166
    message_log: int = 1494003042415153332
    channel_log: int = 1494003460998430813
    role_log: int = 1494003204042788974
    general_log: int = 1494003136732463165
    voice_log: int = 1494003579395113172
    mute_log: int = 1494003651189149868
    kick_log: int = 1494003725587582997
    ban_log: int = 1494003816625078292
    name_log: int = 1494003883926622378
    debug_log: int = 1494003136732463165
    punishment_log: int = 1494003136732463165
    admin_alerts_log: int = 1494003136732463165

    verification_timeout_minutes: int = 10
    base_messages_per_level: int = 60
    level_increase_rate: float = 0.055


D = Defaults()
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
BACKUPS_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "bot.db"
BADWORDS_PATH = BASE_DIR / "badwords.txt"

LINK_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
INVITE_RE = re.compile(r"(discord\.gg/|discord\.com/invite/)", re.IGNORECASE)
SHORT_LINK_RE = re.compile(r"(bit\.ly|tinyurl\.com|t\.co|goo\.gl|cutt\.ly|shorturl\.at)", re.IGNORECASE)
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", re.UNICODE)


def utc_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def xp_needed(level: int, base: int, rate: float) -> int:
    return int(base * ((1 + rate) ** level))


class DB:
    def __init__(self, path: Path):
        DATA_DIR.mkdir(exist_ok=True)
        BACKUPS_DIR.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        c = self.conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            xp INTEGER NOT NULL DEFAULT 0,
            level INTEGER NOT NULL DEFAULT 0,
            messages INTEGER NOT NULL DEFAULT 0,
            warnings INTEGER NOT NULL DEFAULT 0,
            last_xp_ts INTEGER NOT NULL DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS verify_deadlines(
            user_id INTEGER PRIMARY KEY,
            deadline_ts INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS message_history(
            user_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            ts INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS level_history(
            user_id INTEGER NOT NULL,
            ts INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS settings(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS config(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS logged_events(
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            ts INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS cmd_track(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            command TEXT NOT NULL,
            ts INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS bot_blacklist(
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            ts INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS punishments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            moderator_id INTEGER,
            ts INTEGER NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS nick_changes(
            user_id INTEGER NOT NULL,
            ts INTEGER NOT NULL
        )""")
        self.conn.commit()
        self.bootstrap_defaults()

    def bootstrap_defaults(self):
        defaults = {
            "guild_id": str(D.guild_id),
            "owner_id": str(D.owner_id),
            "verification_channel": str(D.verification_channel),
            "verification_log": str(D.verification_log),
            "chat_channel": str(D.chat_channel),
            "level_channel": str(D.level_channel),
            "reviews_channel": str(D.reviews_channel),
            "suggestions_channel": str(D.suggestions_channel),
            "support_suggestions_channel": str(D.support_suggestions_channel),
            "automod_log": str(D.automod_log),
            "message_log": str(D.message_log),
            "channel_log": str(D.channel_log),
            "role_log": str(D.role_log),
            "general_log": str(D.general_log),
            "voice_log": str(D.voice_log),
            "name_log": str(D.name_log),
            "punishment_log": str(D.punishment_log),
            "debug_log": str(D.debug_log),
            "admin_alerts_log": str(D.admin_alerts_log),
            "unverified_role": str(D.unverified_role),
            "verified_role": str(D.verified_role),
            "member_role": str(D.member_role),
            "verification_timeout_minutes": str(D.verification_timeout_minutes),
            "base_messages_per_level": str(D.base_messages_per_level),
            "level_increase_rate": str(D.level_increase_rate),
            "new_account_min_days": "2",
            "new_account_action": "alert",
            "anti_spam_threshold": "6",
            "anti_spam_seconds": "6",
            "anti_caps_ratio": "0.75",
            "anti_caps_min_len": "12",
            "anti_emoji_max": "8",
            "cross_spam_window_sec": "20",
            "name_change_limit_count": "4",
            "name_change_limit_window_hours": "24",
            "load_protection_msgs_per_10s": "120",
            "perf_cpu_limit": "90",
            "perf_ram_limit": "90",
            "sync_channels": "[]",
        }
        settings_defaults = {
            "anti_link": "1",
            "anti_spam": "1",
            "leveling": "1",
            "freeze_xp": "0",
            "load_shedding": "0",
        }
        c = self.conn.cursor()
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO config(key, value) VALUES(?, ?)", (k, v))
        for k, v in settings_defaults.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, v))
        self.conn.commit()

    def get_config(self, key: str, cast=str):
        r = self.conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        if not r:
            raise KeyError(key)
        v = r["value"]
        if cast is bool:
            return v == "1"
        return cast(v)

    def set_config(self, key: str, value):
        self.conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES(?, ?)", (key, str(value)))
        self.conn.commit()

    def get_setting(self, key: str) -> bool:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return (r and r["value"] == "1")

    def set_setting(self, key: str, enabled: bool):
        self.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, "1" if enabled else "0"))
        self.conn.commit()

    def ensure_user(self, user_id: int):
        self.conn.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        self.conn.commit()

    def get_user(self, user_id: int):
        self.ensure_user(user_id)
        return self.conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

    def add_xp_msg(self, user_id: int, now: int):
        self.ensure_user(user_id)
        self.conn.execute(
            "UPDATE users SET xp=xp+1,messages=messages+1,last_xp_ts=? WHERE user_id=?",
            (now, user_id),
        )
        self.conn.execute("INSERT INTO level_history(user_id, ts) VALUES(?, ?)", (user_id, now))
        self.conn.commit()

    def set_level_xp(self, user_id: int, level: int, xp: int):
        self.conn.execute("UPDATE users SET level=?, xp=? WHERE user_id=?", (level, xp, user_id))
        self.conn.commit()

    def set_verify_deadline(self, user_id: int, deadline_ts: int):
        self.conn.execute("INSERT OR REPLACE INTO verify_deadlines(user_id,deadline_ts) VALUES(?,?)", (user_id, deadline_ts))
        self.conn.commit()

    def clear_verify_deadline(self, user_id: int):
        self.conn.execute("DELETE FROM verify_deadlines WHERE user_id=?", (user_id,))
        self.conn.commit()

    def due_verifications(self, now_ts: int):
        return self.conn.execute("SELECT user_id FROM verify_deadlines WHERE deadline_ts<=?", (now_ts,)).fetchall()

    def top(self, since_ts: int = 0):
        if since_ts == 0:
            return self.conn.execute("SELECT user_id,messages,level FROM users ORDER BY messages DESC LIMIT 10").fetchall()
        return self.conn.execute(
            """
            SELECT u.user_id, COUNT(h.user_id) AS messages, u.level
            FROM users u
            LEFT JOIN level_history h ON h.user_id=u.user_id AND h.ts>=?
            GROUP BY u.user_id
            ORDER BY messages DESC
            LIMIT 10
            """,
            (since_ts,),
        ).fetchall()

    def add_warning(self, user_id: int) -> int:
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET warnings=warnings+1 WHERE user_id=?", (user_id,))
        self.conn.commit()
        return self.get_user(user_id)["warnings"]

    def clear_warnings(self, user_id: int):
        self.ensure_user(user_id)
        self.conn.execute("UPDATE users SET warnings=0 WHERE user_id=?", (user_id,))
        self.conn.commit()

    def add_msg_history(self, user_id: int, channel_id: int, content_hash: str, now: int):
        self.conn.execute(
            "INSERT INTO message_history(user_id,channel_id,content_hash,ts) VALUES(?,?,?,?)",
            (user_id, channel_id, content_hash, now),
        )
        self.conn.execute("DELETE FROM message_history WHERE ts<?", (now - 1800,))
        self.conn.commit()

    def cross_spam_count(self, user_id: int, content_hash: str, since_ts: int) -> int:
        r = self.conn.execute(
            "SELECT COUNT(DISTINCT channel_id) AS c FROM message_history WHERE user_id=? AND content_hash=? AND ts>=?",
            (user_id, content_hash, since_ts),
        ).fetchone()
        return r["c"] if r else 0

    def log_event_once(self, event_id: str, event_type: str) -> bool:
        try:
            self.conn.execute("INSERT INTO logged_events(event_id,event_type,ts) VALUES(?,?,?)", (event_id, event_type, utc_ts()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def track_command(self, user_id: int, command: str):
        self.conn.execute("INSERT INTO cmd_track(user_id,command,ts) VALUES(?,?,?)", (user_id, command, utc_ts()))
        self.conn.commit()

    def is_blacklisted(self, user_id: int) -> bool:
        r = self.conn.execute("SELECT 1 FROM bot_blacklist WHERE user_id=?", (user_id,)).fetchone()
        return r is not None

    def add_blacklist(self, user_id: int, reason: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO bot_blacklist(user_id,reason,ts) VALUES(?,?,?)",
            (user_id, reason, utc_ts()),
        )
        self.conn.commit()

    def remove_blacklist(self, user_id: int):
        self.conn.execute("DELETE FROM bot_blacklist WHERE user_id=?", (user_id,))
        self.conn.commit()

    def add_punishment(self, user_id: int, action: str, reason: str, moderator_id: int):
        self.conn.execute(
            "INSERT INTO punishments(user_id,action,reason,moderator_id,ts) VALUES(?,?,?,?,?)",
            (user_id, action, reason, moderator_id, utc_ts()),
        )
        self.conn.commit()

    def add_nick_change(self, user_id: int):
        self.conn.execute("INSERT INTO nick_changes(user_id,ts) VALUES(?,?)", (user_id, utc_ts()))
        self.conn.commit()

    def nick_changes_recent(self, user_id: int, since_ts: int) -> int:
        r = self.conn.execute("SELECT COUNT(*) AS c FROM nick_changes WHERE user_id=? AND ts>=?", (user_id, since_ts)).fetchone()
        return r["c"] if r else 0

    def dump_config_json(self) -> str:
        rows = self.conn.execute("SELECT key,value FROM config ORDER BY key").fetchall()
        return json.dumps({r['key']: r['value'] for r in rows}, ensure_ascii=False, indent=2)


class TTLCache:
    def __init__(self, ttl_sec: int = 30):
        self.ttl_sec = ttl_sec
        self.data = {}

    def get(self, key):
        val = self.data.get(key)
        if not val:
            return None
        expires, payload = val
        if utc_ts() > expires:
            self.data.pop(key, None)
            return None
        return payload

    def set(self, key, payload):
        self.data[key] = (utc_ts() + self.ttl_sec, payload)


class BanConfirm(discord.ui.View):
    def __init__(self, bot: "HubBot", actor_id: int, target: discord.Member, reason: str):
        super().__init__(timeout=45)
        self.bot = bot
        self.actor_id = actor_id
        self.target = target
        self.reason = reason

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.actor_id

    @discord.ui.button(label="✅ تأكيد", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await self.target.ban(reason=self.reason)
        self.bot.db.add_punishment(self.target.id, "ban", self.reason, interaction.user.id)
        await self.bot.punishment_log(self.target.id, "BAN", self.reason, interaction.user.id)
        await interaction.response.edit_message(content=f"✅ تم باند {self.target}", view=None)

    @discord.ui.button(label="❌ إلغاء", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _button: discord.ui.Button):
        await interaction.response.edit_message(content="تم الإلغاء", view=None)


class VerifyView(discord.ui.View):
    def __init__(self, bot: "HubBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="✅ Verify", style=discord.ButtonStyle.success, custom_id="lry_verify_btn")
    async def verify(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        guild = interaction.guild
        member = interaction.user
        unverified = guild.get_role(self.bot.cfg_int("unverified_role"))
        verified = guild.get_role(self.bot.cfg_int("verified_role"))
        normal = guild.get_role(self.bot.cfg_int("member_role"))

        if unverified and unverified in member.roles:
            await member.remove_roles(unverified, reason="verify")
        if verified:
            await member.add_roles(verified, reason="verify")
        if normal:
            await member.add_roles(normal, reason="verify")
        self.bot.db.clear_verify_deadline(member.id)

        event_id = f"verify:{member.id}"
        if self.bot.db.log_event_once(event_id, "verification"):
            e = discord.Embed(title="Verification Success", color=discord.Color.green())
            e.add_field(name="Member", value=member.mention)
            e.add_field(name="ID", value=str(member.id))
            e.add_field(name="Event ID", value=event_id)
            await self.bot.send_log(self.bot.cfg_int("verification_log"), e)

        try:
            await member.send("أهلاً بك في LRY | Hub ✅\n- استخدم القنوات المخصصة لكل قسم\n- راجع القوانين قبل المشاركة")
        except discord.Forbidden:
            pass

        await interaction.response.send_message("تم التحقق منك ✅", ephemeral=True)


class HubBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.voice_states = True

        super().__init__(command_prefix="/", intents=intents)
        self.db = DB(DB_PATH)
        self.cache = TTLCache(ttl_sec=20)
        self.badwords = self.load_badwords()
        self.fast_msgs = defaultdict(deque)
        self.global_msgs = deque(maxlen=300)

    def cfg_int(self, key: str) -> int:
        return self.db.get_config(key, int)

    def cfg_float(self, key: str) -> float:
        return self.db.get_config(key, float)

    def cfg_str(self, key: str) -> str:
        return self.db.get_config(key, str)

    def load_badwords(self):
        if not BADWORDS_PATH.exists():
            return set()
        return {ln.strip().lower() for ln in BADWORDS_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()}

    async def setup_hook(self):
        self.add_view(VerifyView(self))
        guild = discord.Object(id=self.cfg_int("guild_id"))
        await self.tree.sync(guild=guild)
        self.verify_timeout_worker.start()
        self.backup_worker.start()
        self.performance_worker.start()

    async def send_log(self, channel_id: int, embed: discord.Embed):
        ch = self.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                return
        if isinstance(ch, discord.TextChannel):
            await ch.send(embed=embed)

    async def debug(self, title: str, desc: str):
        e = discord.Embed(title=title, description=desc[:3500], color=discord.Color.dark_orange())
        await self.send_log(self.cfg_int("debug_log"), e)

    async def admin_alert(self, desc: str):
        e = discord.Embed(title="Admin Alert", description=desc, color=discord.Color.red())
        await self.send_log(self.cfg_int("admin_alerts_log"), e)

    async def punishment_log(self, user_id: int, action: str, reason: str, moderator_id: int):
        e = discord.Embed(title="Punishment", color=discord.Color.red())
        e.add_field(name="User", value=f"<@{user_id}>")
        e.add_field(name="Action", value=action)
        e.add_field(name="Reason", value=reason or "No reason", inline=False)
        e.add_field(name="Moderator", value=f"<@{moderator_id}>")
        await self.send_log(self.cfg_int("punishment_log"), e)

    def overloaded(self) -> bool:
        threshold = self.cfg_int("load_protection_msgs_per_10s")
        now = utc_ts()
        while self.global_msgs and now - self.global_msgs[0] > 10:
            self.global_msgs.popleft()
        load_shedding = len(self.global_msgs) >= threshold
        self.db.set_setting("load_shedding", load_shedding)
        return load_shedding

    @tasks.loop(minutes=1)
    async def verify_timeout_worker(self):
        guild = self.get_guild(self.cfg_int("guild_id"))
        if not guild:
            return
        for r in self.db.due_verifications(utc_ts()):
            uid = r["user_id"]
            member = guild.get_member(uid)
            if not member:
                self.db.clear_verify_deadline(uid)
                continue
            unverified = guild.get_role(self.cfg_int("unverified_role"))
            if unverified and unverified in member.roles:
                try:
                    await member.kick(reason="Verification timeout")
                    self.db.add_punishment(member.id, "kick", "verification timeout", self.user.id)
                    await self.punishment_log(member.id, "KICK", "verification timeout", self.user.id)
                except discord.Forbidden:
                    pass
                event_id = f"verify-timeout:{uid}"
                if self.db.log_event_once(event_id, "verify_timeout"):
                    e = discord.Embed(title="Verification Timeout", description=f"{member.mention} kicked", color=discord.Color.red())
                    e.add_field(name="Event ID", value=event_id)
                    await self.send_log(self.cfg_int("verification_log"), e)
            self.db.clear_verify_deadline(uid)

    @tasks.loop(hours=12)
    async def backup_worker(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_db = BACKUPS_DIR / f"bot_{stamp}.db"
        shutil.copy2(DB_PATH, backup_db)
        conf_file = BACKUPS_DIR / f"config_{stamp}.json"
        conf_file.write_text(self.db.dump_config_json(), encoding="utf-8")
        backups = sorted(BACKUPS_DIR.glob("bot_*.db"))
        for old in backups[:-8]:
            old.unlink(missing_ok=True)
        await self.debug("Auto Backup", f"Backup created: {backup_db.name}")

    @tasks.loop(minutes=3)
    async def performance_worker(self):
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
        if cpu >= self.cfg_int("perf_cpu_limit") or ram >= self.cfg_int("perf_ram_limit"):
            await self.admin_alert(f"Performance high: CPU {cpu:.1f}% | RAM {ram:.1f}%")


bot = HubBot()


def is_owner_or_admin(member: discord.Member) -> bool:
    return member.id == bot.cfg_int("owner_id") or member.guild_permissions.manage_guild


def admin_check():
    async def predicate(inter: discord.Interaction):
        if not isinstance(inter.user, discord.Member):
            return False
        if bot.db.is_blacklisted(inter.user.id):
            raise app_commands.CheckFailure("blacklisted")
        if is_owner_or_admin(inter.user):
            return True
        raise app_commands.CheckFailure("admin")

    return app_commands.check(predicate)


def slash_alias(name: str, target_desc: str):
    @bot.tree.command(name=name, description=f"Alias for {target_desc}")
    async def _alias(interaction: discord.Interaction):
        if name == "t":
            await top(interaction, scope="all")
        elif name == "p":
            await profile(interaction, user=None)


def can_run_heavy(interaction: discord.Interaction) -> bool:
    return not bot.db.get_setting("load_shedding") or interaction.user.id == bot.cfg_int("owner_id")


@bot.event
async def on_ready():
    print(f"Ready as {bot.user} ({bot.user.id})")
    guild = bot.get_guild(bot.cfg_int("guild_id"))
    if guild:
        channel = guild.get_channel(bot.cfg_int("verification_channel"))
        if isinstance(channel, discord.TextChannel):
            e = discord.Embed(title="Verification", description="اضغط زر Verify للتفعيل", color=discord.Color.blurple())
            await channel.send(embed=e, view=VerifyView(bot))


@bot.event
async def on_app_command_completion(interaction: discord.Interaction, command: app_commands.Command):
    bot.db.track_command(interaction.user.id, command.qualified_name)


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != bot.cfg_int("guild_id"):
        return
    unverified = member.guild.get_role(bot.cfg_int("unverified_role"))
    if unverified:
        await member.add_roles(unverified, reason="new member")
    deadline = utc_ts() + bot.cfg_int("verification_timeout_minutes") * 60
    bot.db.set_verify_deadline(member.id, deadline)

    days_old = (datetime.now(timezone.utc) - member.created_at).days
    min_days = bot.cfg_int("new_account_min_days")
    if days_old < min_days:
        action = bot.cfg_str("new_account_action")
        await bot.admin_alert(f"New account: {member.mention} age={days_old}d < {min_days}d")
        if action == "kick":
            await member.kick(reason="new account detector")


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.nick != after.nick:
        bot.db.add_nick_change(after.id)
        window_hours = bot.cfg_int("name_change_limit_window_hours")
        count_limit = bot.cfg_int("name_change_limit_count")
        recent = bot.db.nick_changes_recent(after.id, utc_ts() - window_hours * 3600)
        e = discord.Embed(title="Nickname Changed", color=discord.Color.purple())
        e.add_field(name="User", value=after.mention)
        e.add_field(name="Before", value=before.nick or before.name)
        e.add_field(name="After", value=after.nick or after.name)
        await bot.send_log(bot.cfg_int("name_log"), e)
        if recent > count_limit:
            await bot.admin_alert(f"Name change limit exceeded by {after.mention}: {recent}/{count_limit}")

    if set(before.roles) != set(after.roles):
        await bot.send_log(bot.cfg_int("role_log"), discord.Embed(title="Role Update", description=after.mention, color=discord.Color.blue()))


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    desc = None
    if before.channel is None and after.channel is not None:
        desc = f"{member} joined {after.channel.mention}"
    elif before.channel is not None and after.channel is None:
        desc = f"{member} left {before.channel.mention}"
    elif before.channel and after.channel and before.channel.id != after.channel.id:
        desc = f"{member} moved to {after.channel.mention}"
    if desc:
        await bot.send_log(bot.cfg_int("voice_log"), discord.Embed(title="Voice Update", description=desc, color=discord.Color.light_grey()))


@bot.event
async def on_message_delete(msg: discord.Message):
    if not msg.guild or not msg.author or msg.author.bot:
        return
    e = discord.Embed(title="Message Deleted", color=discord.Color.red())
    e.add_field(name="User", value=msg.author.mention)
    e.add_field(name="Channel", value=msg.channel.mention)
    e.add_field(name="Content", value=(msg.content or "(empty)")[:1000], inline=False)
    await bot.send_log(bot.cfg_int("message_log"), e)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not after.guild or not after.author or after.author.bot or before.content == after.content:
        return
    e = discord.Embed(title="Message Edited", color=discord.Color.orange())
    e.add_field(name="User", value=after.author.mention)
    e.add_field(name="Channel", value=after.channel.mention)
    e.add_field(name="Before", value=(before.content or "(empty)")[:1000], inline=False)
    e.add_field(name="After", value=(after.content or "(empty)")[:1000], inline=False)
    await bot.send_log(bot.cfg_int("message_log"), e)


@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.author.bot or not isinstance(message.author, discord.Member):
        return

    now = utc_ts()
    bot.global_msgs.append(now)
    _ = bot.overloaded()

    member = message.author
    content = message.content or ""
    low = content.lower()

    unverified = message.guild.get_role(bot.cfg_int("unverified_role"))
    if unverified and unverified in member.roles and message.channel.id != bot.cfg_int("verification_channel"):
        await message.delete()
        await message.channel.send(f"{member.mention} توجه إلى روم التحقق <#{bot.cfg_int('verification_channel')}>", delete_after=5)
        return

    anti_link = bot.db.get_setting("anti_link")
    if anti_link and member.id != bot.cfg_int("owner_id"):
        if INVITE_RE.search(content):
            await message.delete()
            bot.db.add_warning(member.id)
            await bot.admin_alert(f"Invite link deleted from {member.mention}")
            return
        if SHORT_LINK_RE.search(content):
            await message.delete()
            await bot.admin_alert(f"Short-link deleted from {member.mention}")
            return
        if LINK_RE.search(content):
            await message.delete()
            await bot.send_log(bot.cfg_int("automod_log"), discord.Embed(title="Anti-Link", description=f"Deleted link from {member.mention}", color=discord.Color.orange()))
            return

    hit = next((w for w in bot.badwords if w in low), None)
    if hit:
        await message.delete()
        e = discord.Embed(title="Blacklist Word", description=f"Deleted msg from {member.mention}", color=discord.Color.red())
        e.add_field(name="Word", value=hit)
        await bot.send_log(bot.cfg_int("automod_log"), e)
        return

    caps_min_len = bot.cfg_int("anti_caps_min_len")
    if len(content) >= caps_min_len:
        alpha = [c for c in content if c.isalpha()]
        if alpha:
            upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
            if upper_ratio >= bot.cfg_float("anti_caps_ratio"):
                await message.delete()
                await bot.admin_alert(f"Anti-caps deleted message from {member.mention}")
                return

    if len(EMOJI_RE.findall(content)) > bot.cfg_int("anti_emoji_max"):
        await message.delete()
        await bot.admin_alert(f"Emoji spam deleted from {member.mention}")
        return

    if bot.db.get_setting("anti_spam"):
        window = bot.fast_msgs[member.id]
        while window and now - window[0] > bot.cfg_int("anti_spam_seconds"):
            window.popleft()
        window.append(now)
        threshold = bot.cfg_int("anti_spam_threshold") + (3 if message.channel.id == bot.cfg_int("chat_channel") else 0)
        if len(window) >= threshold:
            await message.delete()
            bot.db.add_warning(member.id)
            await bot.admin_alert(f"Smart rate-limit triggered for {member.mention}")
            return

    h = str(hash(low.strip()))
    bot.db.add_msg_history(member.id, message.channel.id, h, now)
    if bot.db.cross_spam_count(member.id, h, now - bot.cfg_int("cross_spam_window_sec")) >= 3:
        await message.delete()
        bot.db.add_warning(member.id)
        await bot.admin_alert(f"Cross-spam detected from {member.mention}")
        return

    review_channels = {bot.cfg_int("reviews_channel"), bot.cfg_int("suggestions_channel"), bot.cfg_int("support_suggestions_channel")}
    if message.channel.id in review_channels:
        await message.delete()
        is_review = message.channel.id == bot.cfg_int("reviews_channel")
        e = discord.Embed(title="Review" if is_review else "Suggestion", description=content or "بدون نص", color=discord.Color.blue() if is_review else discord.Color.green())
        e.set_author(name=str(member), icon_url=member.display_avatar.url)
        e.add_field(name="By", value=member.mention)
        sent = await message.channel.send(embed=e)
        for em in (["✅", "❌"] if is_review else ["👍", "👎"]):
            await sent.add_reaction(em)
        return

    if bot.db.get_setting("leveling") and not bot.db.get_setting("freeze_xp") and message.channel.id == bot.cfg_int("chat_channel"):
        data = bot.db.get_user(member.id)
        if now - data["last_xp_ts"] >= 6:
            bot.db.add_xp_msg(member.id, now)
            data = bot.db.get_user(member.id)
            need = xp_needed(data["level"], bot.cfg_int("base_messages_per_level"), bot.cfg_float("level_increase_rate"))
            if data["xp"] >= need:
                bot.db.set_level_xp(member.id, data["level"] + 1, data["xp"] - need)
                ch = message.guild.get_channel(bot.cfg_int("level_channel"))
                if isinstance(ch, discord.TextChannel):
                    await ch.send(f"🎉 مبروك {member.mention} وصلت لفل {data['level'] + 1}")

    sync_channels = json.loads(bot.cfg_str("sync_channels"))
    if message.channel.id in sync_channels and content.strip():
        for cid in sync_channels:
            if cid == message.channel.id:
                continue
            ch = message.guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                await ch.send(f"[SYNC] {member.display_name}: {content}")


@bot.tree.command(name="help", description="مساعدة الأوامر")
async def help_cmd(interaction: discord.Interaction):
    e = discord.Embed(title="LRY Hub Slash Help", color=discord.Color.blurple())
    e.description = (
        "`/top /t /profile /p /xp_freeze /remove_xp /restore`\n"
        "`/kick /ban /ban_confirm /unban /timeout /warn /warnings /clearwarn /clear`\n"
        "`/lock /unlock /slowmode /slowoff /hide /unhide /nick /role /removerole /disconnect`\n"
        "`/antispam /antilink /blacklist_add /blacklist_remove /sync_add /sync_remove /config_set`"
    )
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="top", description="ترتيب الأعضاء")
@app_commands.describe(scope="all / d / w / m")
async def top(interaction: discord.Interaction, scope: str = "all"):
    now = utc_ts()
    since = 0
    if scope == "d":
        since = now - 86400
    elif scope == "w":
        since = now - 7 * 86400
    elif scope == "m":
        since = now - 30 * 86400
    rows = bot.db.top(since)
    text = "\n".join([f"**{i+1}.** <@{r['user_id']}> — {r['messages']} msgs | Lvl {r['level']}" for i, r in enumerate(rows)]) or "لا يوجد بيانات"
    await interaction.response.send_message(embed=discord.Embed(title=f"Top ({scope})", description=text, color=discord.Color.gold()))


@bot.tree.command(name="t", description="Alias for /top")
async def top_alias(interaction: discord.Interaction):
    await top(interaction, "all")


@bot.tree.command(name="profile", description="بروفايل عضو")
async def profile(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    cache_key = f"profile:{target.id}"
    data = bot.cache.get(cache_key)
    if data is None:
        u = bot.db.get_user(target.id)
        data = {"level": u["level"], "xp": u["xp"], "messages": u["messages"], "warnings": u["warnings"]}
        bot.cache.set(cache_key, data)
    e = discord.Embed(title=f"Profile: {target}", color=discord.Color.teal())
    for k, v in data.items():
        e.add_field(name=k.capitalize(), value=str(v), inline=True)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="p", description="Alias for /profile")
async def profile_alias(interaction: discord.Interaction):
    await profile(interaction, None)


@bot.tree.command(name="xp_freeze", description="تجميد/تفعيل XP")
@admin_check()
async def xp_freeze(interaction: discord.Interaction, enabled: bool):
    bot.db.set_setting("freeze_xp", enabled)
    await interaction.response.send_message(f"XP Freeze = {'ON' if enabled else 'OFF'}")


@bot.tree.command(name="remove_xp", description="خصم XP من عضو")
@admin_check()
async def remove_xp(interaction: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 100000]):
    u = bot.db.get_user(member.id)
    new_xp = max(u["xp"] - amount, 0)
    bot.db.set_level_xp(member.id, u["level"], new_xp)
    await interaction.response.send_message(f"تم خصم {amount} XP من {member.mention}. المتبقي: {new_xp}")


@bot.tree.command(name="restore", description="استرجاع آخر نسخة احتياطية")
@admin_check()
async def restore(interaction: discord.Interaction):
    backups = sorted(BACKUPS_DIR.glob("bot_*.db"))
    if not backups:
        return await interaction.response.send_message("لا يوجد Backup", ephemeral=True)
    latest = backups[-1]
    await interaction.response.defer(ephemeral=True)
    bot.db.conn.close()
    shutil.copy2(latest, DB_PATH)
    bot.db = DB(DB_PATH)
    await interaction.followup.send(f"✅ تم استرجاع {latest.name}", ephemeral=True)


@bot.tree.command(name="sync_add", description="إضافة روم للمزامنة")
@admin_check()
async def sync_add(interaction: discord.Interaction, channel: discord.TextChannel):
    arr = json.loads(bot.cfg_str("sync_channels"))
    if channel.id not in arr:
        arr.append(channel.id)
    bot.db.set_config("sync_channels", json.dumps(arr))
    await interaction.response.send_message(f"✅ تمت إضافة {channel.mention} للمزامنة")


@bot.tree.command(name="sync_remove", description="إزالة روم من المزامنة")
@admin_check()
async def sync_remove(interaction: discord.Interaction, channel: discord.TextChannel):
    arr = json.loads(bot.cfg_str("sync_channels"))
    arr = [x for x in arr if x != channel.id]
    bot.db.set_config("sync_channels", json.dumps(arr))
    await interaction.response.send_message(f"✅ تمت إزالة {channel.mention} من المزامنة")


@bot.tree.command(name="config_set", description="تعديل قيمة في Config")
@admin_check()
async def config_set(interaction: discord.Interaction, key: str, value: str):
    bot.db.set_config(key, value)
    await interaction.response.send_message(f"✅ {key} = {value}")


@bot.tree.command(name="blacklist_add", description="حظر عضو من استخدام البوت")
@admin_check()
async def blacklist_add(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    bot.db.add_blacklist(member.id, reason)
    await interaction.response.send_message(f"✅ تم حظر {member.mention} من أوامر البوت")


@bot.tree.command(name="blacklist_remove", description="فك حظر عضو من أوامر البوت")
@admin_check()
async def blacklist_remove(interaction: discord.Interaction, member: discord.Member):
    bot.db.remove_blacklist(member.id)
    await interaction.response.send_message(f"✅ تم فك الحظر عن {member.mention}")


@bot.tree.command(name="say", description="رسالة عادية")
async def say(interaction: discord.Interaction, message: str):
    if interaction.user.id != bot.cfg_int("owner_id"):
        return await interaction.response.send_message("Owner only", ephemeral=True)
    await interaction.response.send_message("تم", ephemeral=True)
    await interaction.channel.send(message)


@bot.tree.command(name="esay", description="رسالة Embed")
async def esay(interaction: discord.Interaction, message: str):
    if interaction.user.id != bot.cfg_int("owner_id"):
        return await interaction.response.send_message("Owner only", ephemeral=True)
    await interaction.response.send_message("تم", ephemeral=True)
    await interaction.channel.send(embed=discord.Embed(description=message, color=discord.Color.greyple()))


@bot.tree.command(name="kick", description="طرد عضو")
@admin_check()
async def kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not can_run_heavy(interaction):
        return await interaction.response.send_message("Load protection active, try later", ephemeral=True)
    await member.kick(reason=reason)
    bot.db.add_punishment(member.id, "kick", reason, interaction.user.id)
    await bot.punishment_log(member.id, "KICK", reason, interaction.user.id)
    await interaction.response.send_message(f"✅ تم طرد {member}")


@bot.tree.command(name="ban", description="باند مباشر")
@admin_check()
async def ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not can_run_heavy(interaction):
        return await interaction.response.send_message("Load protection active, try later", ephemeral=True)
    await member.ban(reason=reason)
    bot.db.add_punishment(member.id, "ban", reason, interaction.user.id)
    await bot.punishment_log(member.id, "BAN", reason, interaction.user.id)
    await interaction.response.send_message(f"✅ تم باند {member}")


@bot.tree.command(name="ban_confirm", description="باند مع تأكيد")
@admin_check()
async def ban_confirm(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not can_run_heavy(interaction):
        return await interaction.response.send_message("Load protection active, try later", ephemeral=True)
    view = BanConfirm(bot, interaction.user.id, member, reason)
    await interaction.response.send_message(f"هل أنت متأكد من باند {member.mention}؟", view=view, ephemeral=True)


@bot.tree.command(name="unban", description="فك باند")
@admin_check()
async def unban(interaction: discord.Interaction, user_id: str):
    user = await bot.fetch_user(int(user_id))
    await interaction.guild.unban(user)
    await interaction.response.send_message(f"✅ تم فك الباند عن {user_id}")


@bot.tree.command(name="timeout", description="تايم آوت")
@admin_check()
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320]):
    await member.timeout(timedelta(minutes=minutes), reason="manual timeout")
    bot.db.add_punishment(member.id, "timeout", f"{minutes}m", interaction.user.id)
    await bot.punishment_log(member.id, "TIMEOUT", f"{minutes}m", interaction.user.id)
    await interaction.response.send_message(f"✅ تم عمل timeout لـ {member}")


@bot.tree.command(name="warn", description="تحذير")
@admin_check()
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    count = bot.db.add_warning(member.id)
    extra = ""
    if count >= 7:
        await member.ban(reason="7 warnings")
        extra = " + AutoBan"
    elif count >= 5:
        await member.kick(reason="5 warnings")
        extra = " + AutoKick"
    elif count >= 3:
        await member.timeout(timedelta(minutes=10), reason="3 warnings")
        extra = " + AutoTimeout"
    bot.db.add_punishment(member.id, "warn", reason, interaction.user.id)
    await bot.punishment_log(member.id, "WARN", reason, interaction.user.id)
    await interaction.response.send_message(f"⚠️ {member.mention} warnings = {count}{extra}")


@bot.tree.command(name="warnings", description="عدد التحذيرات")
@admin_check()
async def warnings(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.send_message(f"{member.mention} لديه {bot.db.get_user(member.id)['warnings']} تحذير")


@bot.tree.command(name="clearwarn", description="تصفير التحذيرات")
@admin_check()
async def clearwarn(interaction: discord.Interaction, member: discord.Member):
    bot.db.clear_warnings(member.id)
    await interaction.response.send_message(f"✅ تم تصفير التحذيرات لـ {member.mention}")


@bot.tree.command(name="clear", description="حذف رسائل")
@admin_check()
async def clear(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=count)
    await interaction.followup.send(f"Deleted {len(deleted)} messages", ephemeral=True)


@bot.tree.command(name="lock", description="قفل الروم")
@admin_check()
async def lock(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("🔒 تم قفل الروم")


@bot.tree.command(name="unlock", description="فتح الروم")
@admin_check()
async def unlock(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.send_messages = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("🔓 تم فتح الروم")


@bot.tree.command(name="slowmode", description="تشغيل slowmode")
@admin_check()
async def slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 21600]):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"🐢 Slowmode = {seconds}s")


@bot.tree.command(name="slowoff", description="إيقاف slowmode")
@admin_check()
async def slowoff(interaction: discord.Interaction):
    await interaction.channel.edit(slowmode_delay=0)
    await interaction.response.send_message("✅ تم إيقاف السلو مود")


@bot.tree.command(name="hide", description="إخفاء الروم")
@admin_check()
async def hide(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.view_channel = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("🙈 تم إخفاء الروم")


@bot.tree.command(name="unhide", description="إظهار الروم")
@admin_check()
async def unhide(interaction: discord.Interaction):
    ow = interaction.channel.overwrites_for(interaction.guild.default_role)
    ow.view_channel = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.response.send_message("👀 تم إظهار الروم")


@bot.tree.command(name="nick", description="تغيير نك نيم")
@admin_check()
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str):
    await member.edit(nick=nickname)
    await interaction.response.send_message(f"✅ تم تغيير اسم {member.mention}")


@bot.tree.command(name="role", description="إضافة رتبة")
@admin_check()
async def role(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message(f"✅ تمت إضافة {role.name}")


@bot.tree.command(name="removerole", description="إزالة رتبة")
@admin_check()
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message(f"✅ تمت إزالة {role.name}")


@bot.tree.command(name="disconnect", description="فصل عضو من الصوت")
@admin_check()
async def disconnect(interaction: discord.Interaction, member: discord.Member):
    if member.voice and member.voice.channel:
        await member.move_to(None)
        await interaction.response.send_message(f"✅ تم فصل {member.mention}")
    else:
        await interaction.response.send_message("العضو ليس في روم صوتي", ephemeral=True)


@bot.tree.command(name="announce", description="إرسال إعلان لكل الرومات")
@admin_check()
async def announce(interaction: discord.Interaction, message: str):
    if not can_run_heavy(interaction):
        return await interaction.response.send_message("Load protection active, try later", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    sent = 0
    for ch in interaction.guild.text_channels:
        if ch.permissions_for(interaction.guild.me).send_messages:
            try:
                await ch.send(message)
                sent += 1
            except discord.Forbidden:
                pass
    await interaction.followup.send(f"Sent to {sent} channels", ephemeral=True)


@bot.tree.command(name="embed", description="إرسال embed")
@admin_check()
async def embed(interaction: discord.Interaction, message: str):
    await interaction.response.send_message(embed=discord.Embed(description=message, color=discord.Color.purple()))


@bot.tree.command(name="lockdown", description="قفل كل الرومات")
@admin_check()
async def lockdown(interaction: discord.Interaction):
    if not can_run_heavy(interaction):
        return await interaction.response.send_message("Load protection active, try later", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    for ch in interaction.guild.text_channels:
        ow = ch.overwrites_for(interaction.guild.default_role)
        ow.send_messages = False
        await ch.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.followup.send("🚨 Lockdown enabled", ephemeral=True)


@bot.tree.command(name="unlockdown", description="فتح كل الرومات")
@admin_check()
async def unlockdown(interaction: discord.Interaction):
    if not can_run_heavy(interaction):
        return await interaction.response.send_message("Load protection active, try later", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    for ch in interaction.guild.text_channels:
        ow = ch.overwrites_for(interaction.guild.default_role)
        ow.send_messages = None
        await ch.set_permissions(interaction.guild.default_role, overwrite=ow)
    await interaction.followup.send("✅ Lockdown disabled", ephemeral=True)


@bot.tree.command(name="antispam", description="تشغيل/إيقاف antispam")
@admin_check()
async def antispam(interaction: discord.Interaction, enabled: bool):
    bot.db.set_setting("anti_spam", enabled)
    await interaction.response.send_message(f"AntiSpam = {'ON' if enabled else 'OFF'}")


@bot.tree.command(name="antilink", description="تشغيل/إيقاف antilink")
@admin_check()
async def antilink(interaction: discord.Interaction, enabled: bool):
    bot.db.set_setting("anti_link", enabled)
    await interaction.response.send_message(f"AntiLink = {'ON' if enabled else 'OFF'}")


@bot.tree.command(name="resetlevels", description="تصفير اللفلات")
@admin_check()
async def resetlevels(interaction: discord.Interaction):
    bot.db.conn.execute("UPDATE users SET xp=0,level=0,messages=0,last_xp_ts=0")
    bot.db.conn.execute("DELETE FROM level_history")
    bot.db.conn.commit()
    await interaction.response.send_message("✅ تم تصفير جميع اللفلات")


@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        msg = "ليس لديك صلاحية أو أنت في blacklist"
        if not interaction.response.is_done():
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            await interaction.followup.send(msg, ephemeral=True)
        return
    await bot.debug("Command Error", f"{type(error).__name__}: {error}")
    if not interaction.response.is_done():
        await interaction.response.send_message("حدث خطأ داخلي", ephemeral=True)


TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing in .env")

bot.run(TOKEN)
