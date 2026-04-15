import asyncio
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
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

    verification_timeout_minutes: int = 10
    anti_spam_threshold: int = 6
    anti_spam_seconds: int = 6
    xp_cooldown_seconds: int = 6
    base_messages_per_level: int = 60
    level_increase_rate: float = 0.055


CFG = Config()
DB_PATH = "data/bot.db"
BADWORDS_PATH = "badwords.txt"
LINK_RE = re.compile(r"(https?://|discord\.gg/|www\.)", re.IGNORECASE)


class DB:
    def __init__(self, path: str):
        os.makedirs("data", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self):
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 0,
                messages INTEGER NOT NULL DEFAULT 0,
                last_xp_ts INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS warnings (
                user_id INTEGER PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS msg_history (
                user_id INTEGER NOT NULL,
                ts INTEGER NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS verify_deadlines (
                user_id INTEGER PRIMARY KEY,
                deadline_ts INTEGER NOT NULL
            )
            """
        )
        self.conn.commit()

        self.set_setting_default("anti_link", "1")
        self.set_setting_default("anti_spam", "1")
        self.set_setting_default("leveling", "1")

    def set_setting_default(self, key: str, value: str):
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))
        self.conn.commit()

    def get_setting(self, key: str) -> bool:
        cur = self.conn.cursor()
        row = cur.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return (row["value"] if row else "0") == "1"

    def set_setting(self, key: str, value: bool):
        cur = self.conn.cursor()
        cur.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, "1" if value else "0"))
        self.conn.commit()

    def ensure_user(self, user_id: int):
        cur = self.conn.cursor()
        cur.execute("INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,))
        cur.execute("INSERT OR IGNORE INTO warnings(user_id) VALUES(?)", (user_id,))
        self.conn.commit()

    def get_user(self, user_id: int) -> sqlite3.Row:
        self.ensure_user(user_id)
        cur = self.conn.cursor()
        return cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()

    def add_message_and_xp(self, user_id: int, now_ts: int):
        self.ensure_user(user_id)
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE users SET messages = messages + 1, xp = xp + 1, last_xp_ts = ? WHERE user_id = ?",
            (now_ts, user_id),
        )
        cur.execute("INSERT INTO msg_history(user_id, ts) VALUES(?, ?)", (user_id, now_ts))
        self.conn.commit()

    def set_level_data(self, user_id: int, xp: int, level: int):
        cur = self.conn.cursor()
        cur.execute("UPDATE users SET xp = ?, level = ? WHERE user_id = ?", (xp, level, user_id))
        self.conn.commit()

    def can_gain_xp(self, user_id: int, now_ts: int) -> bool:
        user = self.get_user(user_id)
        return now_ts - user["last_xp_ts"] >= CFG.xp_cooldown_seconds

    def top_users(self, since_ts: int = 0, limit: int = 10):
        cur = self.conn.cursor()
        if since_ts == 0:
            return cur.execute(
                "SELECT user_id, messages, level FROM users ORDER BY messages DESC LIMIT ?", (limit,)
            ).fetchall()
        return cur.execute(
            """
            SELECT u.user_id, COUNT(m.user_id) AS messages, u.level
            FROM users u
            LEFT JOIN msg_history m ON m.user_id = u.user_id AND m.ts >= ?
            GROUP BY u.user_id
            ORDER BY messages DESC
            LIMIT ?
            """,
            (since_ts, limit),
        ).fetchall()

    def warning_count(self, user_id: int) -> int:
        self.ensure_user(user_id)
        cur = self.conn.cursor()
        return cur.execute("SELECT count FROM warnings WHERE user_id = ?", (user_id,)).fetchone()["count"]

    def set_warning_count(self, user_id: int, value: int):
        self.ensure_user(user_id)
        cur = self.conn.cursor()
        cur.execute("UPDATE warnings SET count = ? WHERE user_id = ?", (value, user_id))
        self.conn.commit()

    def add_warning(self, user_id: int) -> int:
        value = self.warning_count(user_id) + 1
        self.set_warning_count(user_id, value)
        return value

    def set_verify_deadline(self, user_id: int, deadline_ts: int):
        cur = self.conn.cursor()
        cur.execute("INSERT OR REPLACE INTO verify_deadlines(user_id, deadline_ts) VALUES(?, ?)", (user_id, deadline_ts))
        self.conn.commit()

    def clear_verify_deadline(self, user_id: int):
        cur = self.conn.cursor()
        cur.execute("DELETE FROM verify_deadlines WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def due_verification_kicks(self, now_ts: int):
        cur = self.conn.cursor()
        return cur.execute("SELECT user_id FROM verify_deadlines WHERE deadline_ts <= ?", (now_ts,)).fetchall()

    def reset_levels(self):
        cur = self.conn.cursor()
        cur.execute("UPDATE users SET xp = 0, level = 0, messages = 0, last_xp_ts = 0")
        cur.execute("DELETE FROM msg_history")
        self.conn.commit()


class VerifyView(discord.ui.View):
    def __init__(self, bot: "HubBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="✅ Verify", style=discord.ButtonStyle.success, custom_id="hub_verify_btn")
    async def verify(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return

        member = interaction.user
        guild = interaction.guild

        unverified = guild.get_role(CFG.unverified_role)
        verified = guild.get_role(CFG.verified_role)
        member_role = guild.get_role(CFG.member_role)

        if unverified and unverified in member.roles:
            await member.remove_roles(unverified, reason="Verified by button")
        if verified:
            await member.add_roles(verified, reason="Verified")
        if member_role:
            await member.add_roles(member_role, reason="Verified")

        self.bot.db.clear_verify_deadline(member.id)
        await interaction.response.send_message("تم التحقق منك ✅", ephemeral=True)

        embed = discord.Embed(title="Verification Success", color=discord.Color.green(), timestamp=datetime.now(timezone.utc))
        embed.add_field(name="Member", value=member.mention, inline=True)
        embed.add_field(name="ID", value=str(member.id), inline=True)
        await self.bot.send_log(CFG.verification_log, embed)


class HubBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.messages = True
        intents.message_content = True
        intents.guilds = True
        intents.voice_states = True

        super().__init__(command_prefix="/", intents=intents)
        self.db = DB(DB_PATH)
        self.badwords = self.load_badwords()
        self.spam_windows: dict[int, list[int]] = {}

    def load_badwords(self) -> set[str]:
        if not os.path.exists(BADWORDS_PATH):
            return set()
        with open(BADWORDS_PATH, "r", encoding="utf-8") as f:
            return {line.strip().lower() for line in f if line.strip()}

    async def setup_hook(self):
        self.add_view(VerifyView(self))
        guild_obj = discord.Object(id=CFG.guild_id)
        self.tree.copy_global_to(guild=guild_obj)
        await self.tree.sync(guild=guild_obj)
        self.verify_kick_worker.start()

    async def send_log(self, channel_id: int, embed: discord.Embed):
        ch = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
        if isinstance(ch, discord.TextChannel):
            await ch.send(embed=embed)

    @tasks.loop(minutes=1)
    async def verify_kick_worker(self):
        guild = self.get_guild(CFG.guild_id)
        if not guild:
            return
        now_ts = int(datetime.now(timezone.utc).timestamp())
        due = self.db.due_verification_kicks(now_ts)
        for row in due:
            user_id = row["user_id"]
            member = guild.get_member(user_id)
            if not member:
                self.db.clear_verify_deadline(user_id)
                continue
            unverified = guild.get_role(CFG.unverified_role)
            if unverified and unverified in member.roles:
                try:
                    await member.kick(reason="Verification timeout")
                except discord.Forbidden:
                    pass
                embed = discord.Embed(
                    title="Verification Timeout",
                    description=f"{member.mention} kicked (10m no verify)",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                await self.send_log(CFG.verification_log, embed)
            self.db.clear_verify_deadline(user_id)


bot = HubBot()


def xp_needed(level: int) -> int:
    return int(CFG.base_messages_per_level * ((1 + CFG.level_increase_rate) ** level))


def is_admin(member: discord.Member) -> bool:
    return member.id == CFG.owner_id or member.guild_permissions.manage_guild


async def ensure_verify_panel(guild: discord.Guild):
    channel = guild.get_channel(CFG.verification_channel)
    if not isinstance(channel, discord.TextChannel):
        return
    embed = discord.Embed(
        title="Verification",
        description="اضغط Verify للحصول على الرتب",
        color=discord.Color.blurple(),
    )
    await channel.send(embed=embed, view=VerifyView(bot))


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    guild = bot.get_guild(CFG.guild_id)
    if guild:
        await ensure_verify_panel(guild)


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != CFG.guild_id:
        return
    unverified = member.guild.get_role(CFG.unverified_role)
    if unverified:
        await member.add_roles(unverified, reason="Auto Unverified")
    deadline = int((datetime.now(timezone.utc) + timedelta(minutes=CFG.verification_timeout_minutes)).timestamp())
    bot.db.set_verify_deadline(member.id, deadline)


@bot.event
async def on_message(message: discord.Message):
    if not message.guild or message.author.bot or not isinstance(message.author, discord.Member):
        return

    member = message.author
    content = message.content or ""

    unverified = message.guild.get_role(CFG.unverified_role)
    if unverified and unverified in member.roles and message.channel.id != CFG.verification_channel:
        await message.delete()
        await message.channel.send(f"{member.mention} توجه إلى روم التحقق <#{CFG.verification_channel}>", delete_after=6)
        return

    if bot.db.get_setting("anti_link") and member.id != CFG.owner_id and LINK_RE.search(content):
        await message.delete()
        e = discord.Embed(title="Anti-Link", description=f"Deleted link from {member.mention}", color=discord.Color.orange())
        await bot.send_log(CFG.automod_log, e)
        return

    lowered = content.lower()
    hit_word = next((w for w in bot.badwords if w in lowered), None)
    if hit_word:
        await message.delete()
        e = discord.Embed(title="Badword", description=f"Deleted msg from {member.mention}", color=discord.Color.red())
        e.add_field(name="Word", value=hit_word)
        await bot.send_log(CFG.automod_log, e)
        return

    if bot.db.get_setting("anti_spam"):
        now_ts = int(datetime.now(timezone.utc).timestamp())
        window = bot.spam_windows.get(member.id, [])
        window = [ts for ts in window if now_ts - ts <= CFG.anti_spam_seconds]
        window.append(now_ts)
        bot.spam_windows[member.id] = window
        if len(window) >= CFG.anti_spam_threshold:
            await message.delete()
            e = discord.Embed(title="Anti-Spam", description=f"Deleted spam from {member.mention}", color=discord.Color.gold())
            await bot.send_log(CFG.automod_log, e)
            return

    if message.channel.id in {CFG.reviews_channel, CFG.suggestions_channel, CFG.support_suggestions_channel}:
        await message.delete()
        is_review = message.channel.id == CFG.reviews_channel
        e = discord.Embed(
            title="Review" if is_review else "Suggestion",
            description=content or "بدون نص",
            color=discord.Color.blue() if is_review else discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        e.set_author(name=str(member), icon_url=member.display_avatar.url)
        e.add_field(name="By", value=member.mention)
        sent = await message.channel.send(embed=e)
        for emoji in (["✅", "❌"] if is_review else ["👍", "👎"]):
            await sent.add_reaction(emoji)
        return

    if bot.db.get_setting("leveling") and message.channel.id == CFG.chat_channel:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        if bot.db.can_gain_xp(member.id, now_ts):
            bot.db.add_message_and_xp(member.id, now_ts)
            data = bot.db.get_user(member.id)
            needed = xp_needed(data["level"])
            if data["xp"] >= needed:
                new_level = data["level"] + 1
                new_xp = data["xp"] - needed
                bot.db.set_level_data(member.id, new_xp, new_level)
                level_ch = message.guild.get_channel(CFG.level_channel)
                if isinstance(level_ch, discord.TextChannel):
                    await level_ch.send(f"🎉 مبروك {member.mention} وصلت لفل {new_level}")


@bot.event
async def on_message_delete(message: discord.Message):
    if not message.guild or not message.author or message.author.bot:
        return
    e = discord.Embed(title="Message Deleted", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
    e.add_field(name="User", value=message.author.mention)
    e.add_field(name="Channel", value=message.channel.mention)
    e.add_field(name="Content", value=(message.content or "(empty)")[:1000], inline=False)
    await bot.send_log(CFG.message_log, e)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if not after.guild or not after.author or after.author.bot or before.content == after.content:
        return
    e = discord.Embed(title="Message Edited", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
    e.add_field(name="User", value=after.author.mention)
    e.add_field(name="Channel", value=after.channel.mention)
    e.add_field(name="Before", value=(before.content or "(empty)")[:1000], inline=False)
    e.add_field(name="After", value=(after.content or "(empty)")[:1000], inline=False)
    await bot.send_log(CFG.message_log, e)


@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    e = discord.Embed(title="Channel Created", description=f"{channel.name} ({channel.id})", color=discord.Color.green())
    await bot.send_log(CFG.channel_log, e)


@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    e = discord.Embed(title="Channel Deleted", description=f"{channel.name} ({channel.id})", color=discord.Color.red())
    await bot.send_log(CFG.channel_log, e)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.nick != after.nick:
        e = discord.Embed(title="Nickname Changed", color=discord.Color.purple())
        e.add_field(name="User", value=after.mention)
        e.add_field(name="Before", value=before.nick or before.name)
        e.add_field(name="After", value=after.nick or after.name)
        await bot.send_log(CFG.name_log, e)

    if set(before.roles) != set(after.roles):
        e = discord.Embed(title="Role Update", description=after.mention, color=discord.Color.blue())
        await bot.send_log(CFG.role_log, e)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.bot:
        return
    msg = None
    if before.channel is None and after.channel is not None:
        msg = f"{member} joined {after.channel.mention}"
    elif before.channel is not None and after.channel is None:
        msg = f"{member} left {before.channel.mention}"
    elif before.channel and after.channel and before.channel.id != after.channel.id:
        msg = f"{member} moved to {after.channel.mention}"
    if msg:
        e = discord.Embed(title="Voice Update", description=msg, color=discord.Color.light_grey())
        await bot.send_log(CFG.voice_log, e)


@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.command(name="help", description="عرض قائمة الأوامر")
async def slash_help(interaction: discord.Interaction):
    e = discord.Embed(title="Hub Help", color=discord.Color.blurple())
    e.description = (
        "`/top` `/profile` `/kick` `/ban` `/unban` `/timeout` `/warn` `/warnings` `/clearwarn`\n"
        "`/clear` `/lock` `/unlock` `/slowmode` `/slowoff` `/hide` `/unhide`\n"
        "`/nick` `/role` `/removerole` `/disconnect` `/announce` `/embed`\n"
        "`/lockdown` `/unlockdown` `/antispam` `/antilink` `/resetlevels` `/say` `/esay`"
    )
    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="top", description="قائمة أعلى الأعضاء")
@app_commands.describe(scope="all / d / w / m")
async def top(interaction: discord.Interaction, scope: Optional[str] = "all"):
    now = int(datetime.now(timezone.utc).timestamp())
    since = 0
    if scope == "d":
        since = now - 86400
    elif scope == "w":
        since = now - 7 * 86400
    elif scope == "m":
        since = now - 30 * 86400

    rows = bot.db.top_users(since_ts=since)
    text = "\n".join([f"**{i+1}.** <@{r['user_id']}> — {r['messages']} msgs | Lvl {r['level']}" for i, r in enumerate(rows)]) or "لا يوجد بيانات"
    e = discord.Embed(title=f"Top ({scope})", description=text, color=discord.Color.gold())
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="profile", description="عرض بيانات العضو")
async def profile(interaction: discord.Interaction, user: Optional[discord.Member] = None):
    target = user or interaction.user
    data = bot.db.get_user(target.id)
    warnings = bot.db.warning_count(target.id)
    e = discord.Embed(title=f"Profile: {target}", color=discord.Color.teal())
    e.add_field(name="Level", value=str(data["level"]))
    e.add_field(name="XP", value=str(data["xp"]))
    e.add_field(name="Messages", value=str(data["messages"]))
    e.add_field(name="Warnings", value=str(warnings))
    await interaction.response.send_message(embed=e)


# Owner-only publish
@bot.tree.command(name="say", description="رسالة عادية")
async def say(interaction: discord.Interaction, message: str):
    if interaction.user.id != CFG.owner_id:
        return await interaction.response.send_message("Owner only", ephemeral=True)
    await interaction.response.send_message("تم", ephemeral=True)
    await interaction.channel.send(message)


@bot.tree.command(name="esay", description="رسالة Embed")
async def esay(interaction: discord.Interaction, message: str):
    if interaction.user.id != CFG.owner_id:
        return await interaction.response.send_message("Owner only", ephemeral=True)
    e = discord.Embed(description=message, color=discord.Color.greyple())
    await interaction.response.send_message("تم", ephemeral=True)
    await interaction.channel.send(embed=e)


def admin_only():
    async def predicate(interaction: discord.Interaction):
        if isinstance(interaction.user, discord.Member) and is_admin(interaction.user):
            return True
        raise app_commands.CheckFailure("Admin only")

    return app_commands.check(predicate)


@bot.tree.command(name="kick", description="طرد عضو")
@admin_only()
async def kick(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason"):
    await member.kick(reason=reason)
    await interaction.response.send_message(f"✅ تم طرد {member}")


@bot.tree.command(name="ban", description="باند عضو")
@admin_only()
async def ban(interaction: discord.Interaction, member: discord.Member, reason: Optional[str] = "No reason"):
    await member.ban(reason=reason)
    await interaction.response.send_message(f"✅ تم باند {member}")


@bot.tree.command(name="unban", description="فك باند")
@admin_only()
async def unban(interaction: discord.Interaction, user_id: str):
    user = await bot.fetch_user(int(user_id))
    await interaction.guild.unban(user)
    await interaction.response.send_message(f"✅ تم فك الباند عن {user_id}")


@bot.tree.command(name="timeout", description="تايم آوت بالدقائق")
@admin_only()
async def timeout(interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320]):
    await member.timeout(timedelta(minutes=minutes), reason="Manual timeout")
    await interaction.response.send_message(f"✅ Timeout {member} لمدة {minutes} دقيقة")


@bot.tree.command(name="warn", description="تحذير عضو")
@admin_only()
async def warn(interaction: discord.Interaction, member: discord.Member):
    count = bot.db.add_warning(member.id)
    action = ""
    if count >= 7:
        await member.ban(reason="Auto-ban by 7 warns")
        action = " + AutoBan"
    elif count >= 5:
        await member.kick(reason="Auto-kick by 5 warns")
        action = " + AutoKick"
    elif count >= 3:
        await member.timeout(timedelta(minutes=10), reason="Auto-timeout by 3 warns")
        action = " + AutoTimeout"
    await interaction.response.send_message(f"⚠️ {member} warnings = {count}{action}")


@bot.tree.command(name="warnings", description="عدد التحذيرات")
@admin_only()
async def warnings(interaction: discord.Interaction, member: discord.Member):
    await interaction.response.send_message(f"{member} لديه {bot.db.warning_count(member.id)} تحذير")


@bot.tree.command(name="clearwarn", description="تصفير التحذيرات")
@admin_only()
async def clearwarn(interaction: discord.Interaction, member: discord.Member):
    bot.db.set_warning_count(member.id, 0)
    await interaction.response.send_message(f"✅ تم تصفير التحذيرات لـ {member}")


@bot.tree.command(name="clear", description="حذف رسائل")
@admin_only()
async def clear(interaction: discord.Interaction, count: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=count)
    await interaction.followup.send(f"Deleted {len(deleted)} messages", ephemeral=True)


@bot.tree.command(name="lock", description="قفل الروم")
@admin_only()
async def lock(interaction: discord.Interaction):
    overwrites = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrites.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.response.send_message("🔒 تم قفل الروم")


@bot.tree.command(name="unlock", description="فتح الروم")
@admin_only()
async def unlock(interaction: discord.Interaction):
    overwrites = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrites.send_messages = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.response.send_message("🔓 تم فتح الروم")


@bot.tree.command(name="slowmode", description="تشغيل slowmode")
@admin_only()
async def slowmode(interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 21600]):
    await interaction.channel.edit(slowmode_delay=seconds)
    await interaction.response.send_message(f"🐢 Slowmode = {seconds}s")


@bot.tree.command(name="slowoff", description="إيقاف slowmode")
@admin_only()
async def slowoff(interaction: discord.Interaction):
    await interaction.channel.edit(slowmode_delay=0)
    await interaction.response.send_message("✅ تم إيقاف السلو مود")


@bot.tree.command(name="hide", description="إخفاء الروم")
@admin_only()
async def hide(interaction: discord.Interaction):
    overwrites = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrites.view_channel = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.response.send_message("🙈 تم إخفاء الروم")


@bot.tree.command(name="unhide", description="إظهار الروم")
@admin_only()
async def unhide(interaction: discord.Interaction):
    overwrites = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrites.view_channel = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.response.send_message("👀 تم إظهار الروم")


@bot.tree.command(name="nick", description="تغيير نك نيم")
@admin_only()
async def nick(interaction: discord.Interaction, member: discord.Member, nickname: str):
    await member.edit(nick=nickname)
    await interaction.response.send_message(f"✅ تم تغيير اسم {member} إلى {nickname}")


@bot.tree.command(name="role", description="إضافة رتبة")
@admin_only()
async def role(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.add_roles(role)
    await interaction.response.send_message(f"✅ تم إعطاء {role.name} لـ {member}")


@bot.tree.command(name="removerole", description="إزالة رتبة")
@admin_only()
async def removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    await member.remove_roles(role)
    await interaction.response.send_message(f"✅ تمت إزالة {role.name} من {member}")


@bot.tree.command(name="disconnect", description="فصل عضو من الصوت")
@admin_only()
async def disconnect(interaction: discord.Interaction, member: discord.Member):
    if member.voice and member.voice.channel:
        await member.move_to(None)
        await interaction.response.send_message(f"✅ تم فصل {member}")
    else:
        await interaction.response.send_message("العضو ليس في روم صوتي", ephemeral=True)


@bot.tree.command(name="announce", description="إرسال إعلان لكل الرومات النصية")
@admin_only()
async def announce(interaction: discord.Interaction, message: str):
    await interaction.response.defer(ephemeral=True)
    count = 0
    for ch in interaction.guild.text_channels:
        perms = ch.permissions_for(interaction.guild.me)
        if perms.send_messages:
            try:
                await ch.send(message)
                count += 1
            except discord.Forbidden:
                continue
    await interaction.followup.send(f"Sent to {count} channels", ephemeral=True)


@bot.tree.command(name="embed", description="إرسال Embed")
@admin_only()
async def embed(interaction: discord.Interaction, message: str):
    e = discord.Embed(description=message, color=discord.Color.purple())
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="lockdown", description="قفل كل الرومات النصية")
@admin_only()
async def lockdown(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    for ch in interaction.guild.text_channels:
        overwrites = ch.overwrites_for(interaction.guild.default_role)
        overwrites.send_messages = False
        await ch.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.followup.send("🚨 Lockdown enabled", ephemeral=True)


@bot.tree.command(name="unlockdown", description="فتح كل الرومات النصية")
@admin_only()
async def unlockdown(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    for ch in interaction.guild.text_channels:
        overwrites = ch.overwrites_for(interaction.guild.default_role)
        overwrites.send_messages = None
        await ch.set_permissions(interaction.guild.default_role, overwrite=overwrites)
    await interaction.followup.send("✅ Lockdown disabled", ephemeral=True)


@bot.tree.command(name="antispam", description="تفعيل/تعطيل antispam")
@admin_only()
async def antispam(interaction: discord.Interaction, enabled: bool):
    bot.db.set_setting("anti_spam", enabled)
    await interaction.response.send_message(f"AntiSpam = {'ON' if enabled else 'OFF'}")


@bot.tree.command(name="antilink", description="تفعيل/تعطيل antilink")
@admin_only()
async def antilink(interaction: discord.Interaction, enabled: bool):
    bot.db.set_setting("anti_link", enabled)
    await interaction.response.send_message(f"AntiLink = {'ON' if enabled else 'OFF'}")


@bot.tree.command(name="resetlevels", description="تصفير اللفلات")
@admin_only()
async def resetlevels(interaction: discord.Interaction):
    bot.db.reset_levels()
    await interaction.response.send_message("✅ تم تصفير جميع اللفلات")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        if not interaction.response.is_done():
            await interaction.response.send_message("ليس لديك صلاحية لاستخدام هذا الأمر", ephemeral=True)
        else:
            await interaction.followup.send("ليس لديك صلاحية لاستخدام هذا الأمر", ephemeral=True)
        return
    if not interaction.response.is_done():
        await interaction.response.send_message(f"حدث خطأ: {error}", ephemeral=True)


TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to .env")

bot.run(TOKEN)
