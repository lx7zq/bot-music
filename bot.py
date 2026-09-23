import logging

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)
logger.warning("=== NEW VERSION LOADED ===")

import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import io
import os
import gc
import shutil
import stat
import ctypes
import ctypes.util
import urllib.request
import tarfile
import subprocess
from collections import deque
import aiohttp
import time

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:8000")
DASHBOARD_PUBLIC_URL = os.environ.get("DASHBOARD_PUBLIC_URL", "http://localhost:5173")

BIN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
os.makedirs(BIN_DIR, exist_ok=True)


# ── ffmpeg ─────────────────────────────────────────────────────────────────
def _ensure_ffmpeg() -> str:
    logger.warning("[DIAG] หา ffmpeg...")
    found = shutil.which("ffmpeg")
    if found:
        logger.warning(f"[DIAG] ffmpeg in PATH: {found}")
        return found
    local = os.path.join(BIN_DIR, "ffmpeg")
    if os.path.isfile(local) and os.access(local, os.X_OK):
        try:
            result = subprocess.run(
                [local, "-version"], capture_output=True, text=True, timeout=3
            )
            ver = (
                (result.stdout or "").split("version ")[-1].split(" ")[0]
                if "version" in (result.stdout or "")
                else ""
            )
            if not ver.startswith("4."):
                logger.warning(
                    f"[DIAG] ffmpeg version {ver} ไม่ใช่ 4.x — ลบแล้ว re-download"
                )
                os.remove(local)
            else:
                logger.warning(f"[DIAG] ffmpeg local v{ver}: {local}")
                return local
        except Exception:
            pass

    logger.warning("[DIAG] ffmpeg ไม่พบ — กำลัง download...")
    url = (
        "https://johnvansickle.com/ffmpeg/old-releases/ffmpeg-4.4.1-amd64-static.tar.xz"
    )
    tar_path = "/tmp/ffmpeg.tar.xz"
    try:
        urllib.request.urlretrieve(url, tar_path)
        with tarfile.open(tar_path, "r:xz") as t:
            for member in t.getmembers():
                if member.name.endswith("/ffmpeg") and "/" in member.name:
                    member.name = "ffmpeg"
                    t.extract(member, path=BIN_DIR)
                    break
        os.chmod(
            local, os.stat(local).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH
        )
        logger.warning(f"[DIAG] ✅ ffmpeg downloaded: {local}")
        return local
    except Exception as e:
        logger.warning(f"[DIAG] ❌ ffmpeg download failed: {e}")
        return "ffmpeg"


# ── opus ───────────────────────────────────────────────────────────────────
def _ensure_opus() -> None:
    if discord.opus.is_loaded():
        logger.warning("[DIAG] ✅ Opus already loaded")
        return

    paths = [
        "libopus.so.0",
        "libopus.so",
        "opus",
        "/usr/lib/x86_64-linux-gnu/libopus.so.0",
        "/usr/lib/aarch64-linux-gnu/libopus.so.0",
        "/usr/local/lib/libopus.so.0",
    ]
    lib = ctypes.util.find_library("opus")
    if lib:
        paths.insert(0, lib)

    for path in paths:
        try:
            discord.opus.load_opus(path)
            logger.warning(f"[DIAG] ✅ Opus loaded: {path}")
            return
        except Exception:
            pass

    opus_so = os.path.join(BIN_DIR, "libopus.so.0")
    if os.path.isfile(opus_so):
        try:
            discord.opus.load_opus(opus_so)
            logger.warning(f"[DIAG] ✅ Opus loaded from cache: {opus_so}")
            return
        except Exception:
            pass

    logger.warning("[DIAG] Opus ไม่พบ — กำลัง download .deb...")
    deb_url = "http://ftp.debian.org/debian/pool/main/o/opus/libopus0_1.3.1-3_amd64.deb"
    deb_path = "/tmp/libopus0.deb"
    try:
        urllib.request.urlretrieve(deb_url, deb_path)
        with open(deb_path, "rb") as f:
            if f.read(8) != b"!<arch>\n":
                raise ValueError("ไม่ใช่ ar archive")
            while True:
                header = f.read(60)
                if len(header) < 60:
                    break
                name = header[0:16].decode("ascii", errors="replace").strip()
                size = int(header[48:58].decode("ascii").strip())
                data = f.read(size)
                if size % 2 == 1:
                    f.read(1)
                if name.startswith("data.tar"):
                    ext = name.rstrip("/").split(".", 2)[-1]
                    tmp_tar = f"/tmp/opus_data.tar.{ext}"
                    with open(tmp_tar, "wb") as tf:
                        tf.write(data)
                    with tarfile.open(tmp_tar) as t:
                        for member in t.getmembers():
                            if (
                                "libopus.so.0" in member.name
                                and not member.islnk()
                                and not member.issym()
                            ):
                                member.name = "libopus.so.0"
                                t.extract(member, path=BIN_DIR)
                                break
                    break
        os.chmod(
            opus_so,
            os.stat(opus_so).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH,
        )
        discord.opus.load_opus(opus_so)
        logger.warning(f"[DIAG] ✅ Opus downloaded & loaded: {opus_so}")
    except Exception as e:
        logger.warning(f"[DIAG] ❌ Opus download failed: {e}")


FFMPEG_PATH = _ensure_ffmpeg()

try:
    import nacl

    logger.warning(f"[DIAG] PyNaCl: {nacl.__version__}")
except ImportError:
    logger.warning("[DIAG] ❌ PyNaCl NOT installed!")

TOKEN = os.environ.get("TOKEN")

# ── DJ role & dashboard keys ───────────────────────────────────────────────
# ไม่ต้อง login หน้าเว็บ — เช็ค role ตอนกดในดิส (ephemeral) แล้วแจกลิงก์ ?key= ให้
DJ_ROLE_NAME = os.environ.get("DJ_ROLE_NAME", "DJ").lower()
BOT_SECRET = os.environ.get("BOT_SECRET", "change-me-in-prod")

# ── Billing ────────────────────────────────────────────────────────────────
ADMIN_CHANNEL_ID = int(os.environ.get("ADMIN_DISCORD_CHANNEL_ID", "0") or 0)
OWNER_DISCORD_ID = int(os.environ.get("OWNER_DISCORD_ID", "0") or 0)
GRACE_DAYS = int(os.environ.get("BILLING_GRACE_DAYS", "3") or 3)
PRICING_URL = os.environ.get("PRICING_URL", f"{DASHBOARD_PUBLIC_URL}/pricing")
INVITE_CLIENT_ID = os.environ.get("INVITE_CLIENT_ID", "1512172686254800986")
INVITE_PERMS = int(os.environ.get("INVITE_PERMS", "36785152"))  # view+send+embed+history+connect+speak+VAD


def invite_url() -> str:
    return (
        "https://discord.com/oauth2/authorize"
        f"?client_id={INVITE_CLIENT_ID}&permissions={INVITE_PERMS}&scope=bot+applications.commands"
    )


def _dashboard_messages(guild_id: int, key: str | None, dj: bool) -> str:
    """ข้อความแจกลิงก์แบบ masked (ไม่โชว์ key ดิบในห้องแชท)"""
    link = _dashboard_link(guild_id, key)
    if dj:
        return f"[🖥️ เปิด Dashboard (DJ)]({link}) — ลิงก์นี้กดได้ทุกปุ่ม อย่าส่งต่อนะ ♡"
    return (
        f"[🖥️ เปิด Dashboard]({link}) — เปิดดูได้เลยนะ ♡ "
        "ปุ่มคุมเป็นของ DJ อยากขอเพลงใช้ `/play` ในห้องได้ปกติ"
    )


async def _sub_status(guild_id: int) -> dict:
    """ถาม backend ว่าดิสนี้จ่ายอยู่ไหม — backend ล่ม = ปล่อยผ่าน (fail-open) แล้ว log"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{DASHBOARD_URL}/internal/subscription/{guild_id}",
                headers={"X-Bot-Secret": BOT_SECRET},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    return await r.json()
    except Exception as e:
        logger.warning(f"[BILL] sub check failed (fail-open): {e}")
    return {"paid": True, "paid_until": None, "offline": True}


def _expired_msg(paid_until) -> str:
    when = f" (หมดอายุ {paid_until})" if paid_until else ""
    return (
        f"˚⋆ แพ็กเกจหมดอายุแล้ว{when} ♡ ต่ออายุ 99฿/เดือนที่ {PRICING_URL} "
        f"แล้วส่งสลิปได้เลย (ใช้ต่อได้อีก {GRACE_DAYS} วันหลังหมดอายุนะ)"
    )


def _blocked_msg(sub: dict) -> str | None:
    """คืน None = เล่นได้ / คืนข้อความ = โดนบล็อก (แยกเคส trial vs หมดแพ็กเกจ)"""
    if sub.get("paid") or sub.get("in_grace") or sub.get("trial"):
        return None
    if sub.get("trial_expired"):
        return (
            "˚⋆ หมดช่วงทดลองใช้แล้ว ♡ ต่อแค่ 99฿/เดือนที่ "
            f"{PRICING_URL} ส่งสลิปหน้าเว็บได้เลย"
        )
    return _expired_msg(sub.get("paid_until"))


def is_admin(interaction: discord.Interaction) -> bool:
    """เจ้าของดิส / MANAGE_GUILD / ADMINISTRATOR — ไม่มีวันโดนล็อกเอง"""
    if not interaction.guild:
        return False
    user = interaction.user
    if user.id == interaction.guild.owner_id:
        return True
    perms = getattr(user, "guild_permissions", None)
    if perms is not None and (perms.administrator or perms.manage_guild):
        return True
    return False


def has_dj(interaction: discord.Interaction) -> bool:
    """แอดมินผ่านอัตโนมัติ / นอกนั้นต้องมี role ชื่อ DJ"""
    if is_admin(interaction):
        return True
    roles = getattr(interaction.user, "roles", []) or []
    return any((getattr(r, "name", "") or "").lower() == DJ_ROLE_NAME for r in roles)


def _can_control(interaction: discord.Interaction) -> bool:
    """ปุ่มเขียว: DJ หรือคนในห้องเสียงเดียวกับบอท"""
    return has_dj(interaction) or _is_same_channel(interaction)


async def _deny(
    interaction: discord.Interaction,
    msg: str = "˚⋆ ต้องมี role DJ หรืออยู่ในห้องเสียงเดียวกับบอทนะ ♡",
):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass


async def _fetch_dashboard_key(guild_id: int) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{DASHBOARD_URL}/internal/key/{guild_id}",
                headers={"X-Bot-Secret": BOT_SECRET},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    return (await r.json()).get("key")
    except Exception as e:
        logger.warning(f"[DASH] key fetch failed: {e}")
    return None


async def _rotate_dashboard_key(guild_id: int) -> str | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{DASHBOARD_URL}/internal/rotate/{guild_id}",
                headers={"X-Bot-Secret": BOT_SECRET},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                if r.status == 200:
                    return (await r.json()).get("key")
    except Exception as e:
        logger.warning(f"[DASH] key rotate failed: {e}")
    return None


def _dashboard_link(guild_id: int, key: str | None) -> str:
    base = f"{DASHBOARD_PUBLIC_URL}/guild/{guild_id}"
    return f"{base}?key={key}" if key else base

YDL_SEARCH = {
    "format": "worstaudio/bestaudio[abr<=64]/bestaudio",
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "noplaylist": True,
    "skip_download": True,
    "no_color": True,
    "ignoreerrors": True,
    "no_cache_dir": True,
    "socket_timeout": 15,
}

YDL_STREAM = {
    "format": "worstaudio/bestaudio[abr<=64]/bestaudio",
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "noplaylist": True,
    "skip_download": True,
    "no_color": True,
    "no_cache_dir": True,
    "socket_timeout": 15,
}

FFMPEG_OPTIONS = {
    "executable": FFMPEG_PATH,
    "before_options": (
        "-reconnect 1 -reconnect_streamed 1 " "-reconnect_delay_max 5 " "-nostdin "
    ),
    "options": "-vn -loglevel warning",
}

intents = discord.Intents.none()
intents.guilds = True
intents.guild_messages = True
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="§", intents=intents)
tree = bot.tree

queues: dict[int, deque] = {}
now_playing: dict[int, dict] = {}
now_playing_msg: dict[int, discord.Message] = {}

skip_votes: dict[int, set] = {}
announce_channels: dict[int, discord.abc.Messageable] = {}
VOTE_SKIP_THRESHOLD = 2

_idle_timers: dict[int, asyncio.Task] = {}
AUTO_DISCONNECT_DELAY = 180


async def _auto_disconnect(
    guild_id: int, voice_client, delay: int = AUTO_DISCONNECT_DELAY
):
    await asyncio.sleep(delay)
    if voice_client and voice_client.is_connected():
        if not voice_client.is_playing() and not voice_client.is_paused():
            logger.warning(
                f"[AUTO-DC] Guild {guild_id} เงียบนาน {delay}s — กำลังออก..."
            )
            queues.pop(guild_id, None)
            now_playing.pop(guild_id, None)
            await voice_client.disconnect()


def _reset_idle_timer(guild_id: int, voice_client):
    if guild_id in _idle_timers:
        _idle_timers[guild_id].cancel()
    if voice_client and voice_client.is_connected():
        _idle_timers[guild_id] = asyncio.create_task(
            _auto_disconnect(guild_id, voice_client)
        )
        logger.warning(f"[AUTO-DC] Guild {guild_id} เริ่มนับ {AUTO_DISCONNECT_DELAY}s")


def _cancel_idle_timer(guild_id: int):
    if guild_id in _idle_timers:
        _idle_timers[guild_id].cancel()
        _idle_timers.pop(guild_id, None)


def get_queue(guild_id: int) -> deque:
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


def fmt_duration(seconds) -> str:
    mins, secs = divmod(int(seconds or 0), 60)
    return f"{mins}:{secs:02d}"


def queue_total_duration(queue: deque) -> str:
    total = sum(s.get("duration", 0) for s in queue)
    hours, remainder = divmod(int(total), 3600)
    mins, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


async def fetch_songs(query: str, limit: int = 1) -> list[dict]:
    search_query = query if query.startswith("http") else f"ytsearch{limit}:{query}"

    def _fetch():
        with yt_dlp.YoutubeDL(YDL_SEARCH) as ydl:
            info = ydl.extract_info(search_query, download=False)
            if not info:
                return []
            entries = info.get("entries", [info])
            result = []
            for e in entries:
                if not e:
                    continue
                result.append(
                    {
                        "title": e.get("title", "ไม่ทราบชื่อ"),
                        "webpage_url": e.get("webpage_url", ""),
                        "duration": e.get("duration", 0),
                        "thumbnail": e.get("thumbnail", ""),
                    }
                )
            return result[:limit]

    try:
        songs = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        gc.collect()
        return songs
    except Exception as e:
        logger.warning(f"fetch_songs error: {e}")
        return []


async def fetch_playlist_with_progress(
    query: str, msg: discord.Message, limit: int = 50
) -> list[dict]:
    result = []

    def _fetch():
        ydl_opts = {**YDL_SEARCH, "noplaylist": False}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if not info:
                return []
            entries = info.get("entries", [info])
            return [e for e in entries if e][:limit]

    try:
        raw = await asyncio.get_event_loop().run_in_executor(None, _fetch)
        total = len(raw)
        for i, e in enumerate(raw, 1):
            result.append(
                {
                    "title": e.get("title", "ไม่ทราบชื่อ"),
                    "webpage_url": e.get("webpage_url", ""),
                    "duration": e.get("duration", 0),
                    "thumbnail": e.get("thumbnail", ""),
                }
            )
            if i % 5 == 0 or i == total:
                try:
                    await msg.edit(
                        content=f"˚⋆𐙚 กำลังโหลด Playlist... **{i}/{total}** เพลง ♡"
                    )
                except Exception:
                    pass
        gc.collect()
        return result
    except Exception as e:
        logger.warning(f"fetch_playlist error: {e}")
        return []


# ── Helpers ────────────────────────────────────────────────────────────────


def _get_voice_client(interaction: discord.Interaction):
    return interaction.guild.voice_client if interaction.guild else None


def _is_same_channel(interaction: discord.Interaction) -> bool:
    vc = _get_voice_client(interaction)
    if not vc:
        return False
    return (
        interaction.user.voice is not None
        and interaction.user.voice.channel == vc.channel
    )


async def _ensure_voice(interaction: discord.Interaction) -> discord.VoiceClient | None:
    if not interaction.user.voice:
        await interaction.followup.send(
            "˚⋆ เข้า Voice Channel ก่อนนะ ♡", ephemeral=True
        )
        return None
    vc = _get_voice_client(interaction)
    if vc:
        return vc
    return await interaction.user.voice.channel.connect(
        reconnect=True, self_deaf=False, self_mute=False
    )


async def _delete_now_playing_msg(guild_id: int):
    old_msg = now_playing_msg.pop(guild_id, None)
    if old_msg:
        try:
            await old_msg.delete()
        except Exception:
            pass


# ── Embed ──────────────────────────────────────────────────────────────────


def build_now_playing_embed(
    song: dict,
    queue: deque | None = None,
    guild_id: int | None = None,  # ← เพิ่ม
) -> discord.Embed:
    embed = discord.Embed(
        description=f"### ⋆˚𐙚｡ {song['title']} ｡𐙚˚⋆",
        color=0x5865F2,
        # title ต้องมีถ้าจะใส่ url — ใช้ชื่อสั้น ๆ
        url=f"{DASHBOARD_PUBLIC_URL}/guild/{guild_id}" if guild_id else None,
    )
    embed.add_field(
        name="☁︎ ความยาว", value=fmt_duration(song.get("duration")), inline=True
    )
    embed.add_field(name="♡ ผู้ขอ", value=song.get("requester", "?"), inline=True)

    if queue:
        q_list = list(queue)
        if q_list:
            next_song = q_list[0]
            embed.add_field(
                name="⋆ ถัดไป",
                value=f"{next_song['title']} ({fmt_duration(next_song.get('duration'))})",
                inline=False,
            )

    if song.get("thumbnail"):
        embed.set_thumbnail(url=song["thumbnail"])
    embed.set_footer(text="⋆𐙚˚ กำลังเล่นอยู่นะ ♡ • ใช้ปุ่มด้านล่างเพื่อควบคุม ˚𐙚⋆")
    return embed


# ── Views ──────────────────────────────────────────────────────────────────


class MoveChannelView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, target_channel, query: str):
        super().__init__(timeout=20)
        self.interaction = interaction
        self.target_channel = target_channel
        self.query = query
        self.answered = False

    @discord.ui.button(
        label="𐙚˚⋆ ย้ายและเล่นเพลงเลย", style=discord.ButtonStyle.success
    )
    async def move_yes(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.interaction.user:
            return await interaction.response.send_message(
                "˚⋆ ไม่ใช่คนสั่งนะ ♡", ephemeral=True
            )
        self.answered = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"𐙚˚⋆ ย้ายไปที่ **{self.target_channel.name}** แล้ว ♡ กำลังโหลดเพลง...",
            view=self,
        )

        guild = interaction.guild
        vc = guild.voice_client
        await vc.move_to(self.target_channel)

        songs = await fetch_songs(
            self.query, limit=1 if not self.query.startswith("http") else 50
        )
        if not songs:
            await interaction.followup.send("˚⋆ หาเพลงไม่เจอเลย ♡ ลองใหม่นะ")
            return
        queue = get_queue(guild.id)
        was_playing = vc.is_playing() or vc.is_paused()
        pos_before = len(queue)
        for s in songs:
            s["requester"] = interaction.user.display_name
            queue.append(s)
        if len(songs) > 1:
            await interaction.followup.send(
                f"𐙚˚⋆ เพิ่ม **{len(songs)} เพลง** เข้า Queue แล้วนะ ♡"
            )
        elif was_playing:
            await interaction.followup.send(
                f"𐙚˚⋆ เพิ่ม **{songs[0]['title']}** เข้า Queue แล้ว ♡ อยู่ในคิวที่ #{pos_before + 1}"
            )
        if not was_playing:
            played = await _play_next_guild(guild, announce_channel=interaction.channel)
            if played:
                sent = await interaction.followup.send(
                    embed=build_now_playing_embed(
                        played, get_queue(guild.id), guild_id=guild.id
                    ),
                    view=GuildPlayerView(guild),
                )
                now_playing_msg[guild.id] = sent

    @discord.ui.button(label="˚⋆ ไม่ต้องนะ", style=discord.ButtonStyle.danger)
    async def move_no(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user != self.interaction.user:
            return await interaction.response.send_message(
                "˚⋆ ไม่ใช่คนสั่งนะ ♡", ephemeral=True
            )
        self.answered = True
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="˚⋆ โอเค ยกเลิกแล้วนะ ♡", view=self
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class GuildPlayerView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=None)
        self.guild = guild

        # ปุ่มลิงก์กดทีเดียวเปิดเว็บเลย (เช็ค role ไม่ได้ — เลยเป็นลิงก์ดูอย่างเดียว
        # ดู+ขอเพลงได้ปกติ / DJ ขอลิงก์คุมเต็มผ่าน /dashboard)
        self.add_item(
            discord.ui.Button(
                emoji="🖥️",
                label="เปิด Dashboard",
                style=discord.ButtonStyle.link,
                url=_dashboard_link(guild.id, None),
                row=1,
            )
        )

    @discord.ui.button(emoji="⏮", style=discord.ButtonStyle.secondary, row=0)
    async def prev_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not _can_control(interaction):
            return await _deny(interaction)
        vc = self.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.response.send_message(
                "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
            )
        current = now_playing.get(self.guild.id)
        if current:
            get_queue(self.guild.id).appendleft(current)
            vc.stop()
        await interaction.response.send_message(
            "𐙚˚⋆ เริ่มเพลงนี้ใหม่แล้วนะ ♡", ephemeral=True
        )

    @discord.ui.button(emoji="⏸", style=discord.ButtonStyle.primary, row=0)
    async def pause_resume(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not _can_control(interaction):
            return await _deny(interaction)
        vc = self.guild.voice_client
        if not vc:
            return await interaction.response.send_message(
                "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
            )
        if vc.is_playing():
            vc.pause()
            button.emoji = "▶️"
            await interaction.response.edit_message(view=self)
        elif vc.is_paused():
            vc.resume()
            button.emoji = "⏸"
            await interaction.response.edit_message(view=self)
        else:
            await interaction.response.send_message(
                "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
            )

    @discord.ui.button(emoji="⏭", style=discord.ButtonStyle.primary, row=0)
    async def skip_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not _can_control(interaction):
            return await _deny(interaction)
        vc = self.guild.voice_client
        if not vc or (not vc.is_playing() and not vc.is_paused()):
            return await interaction.response.send_message(
                "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
            )

        guild_id = self.guild.id
        current = now_playing.get(guild_id, {})
        requester = current.get("requester", "")
        user_name = interaction.user.display_name

        if user_name == requester:
            skip_votes.pop(guild_id, None)
            await interaction.response.send_message(
                "𐙚˚⋆ ข้ามเพลงแล้วนะ ♡", ephemeral=True
            )
            vc.stop()
            return

        human_members = [m for m in vc.channel.members if not m.bot]
        if len(human_members) < VOTE_SKIP_THRESHOLD:
            skip_votes.pop(guild_id, None)
            await interaction.response.send_message(
                "𐙚˚⋆ ข้ามเพลงแล้วนะ ♡", ephemeral=True
            )
            vc.stop()
            return

        votes = skip_votes.setdefault(guild_id, set())
        votes.add(interaction.user.id)
        needed = max(VOTE_SKIP_THRESHOLD, len(human_members) // 2 + 1)
        if len(votes) >= needed:
            skip_votes.pop(guild_id, None)
            await interaction.response.send_message(
                f"⏭ Vote skip ผ่าน ({len(votes)}/{needed}) — ข้ามเพลงแล้ว!",
                ephemeral=False,
            )
            vc.stop()
        else:
            await interaction.response.send_message(
                f"𐙚˚⋆ โหวตข้ามเพลง **{len(votes)}/{needed}** โหวต", ephemeral=False
            )

    @discord.ui.button(emoji="⏹", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if not has_dj(interaction):
            return await _deny(interaction, "˚⋆ ปุ่มนี้เฉพาะ DJ นะ ♡")
        guild_id = self.guild.id
        queues[guild_id] = deque()
        now_playing.pop(guild_id, None)
        skip_votes.pop(guild_id, None)
        announce_channels.pop(guild_id, None)
        vc = self.guild.voice_client
        if vc:
            vc.stop()
        _reset_idle_timer(guild_id, vc)
        old_msg = now_playing_msg.pop(guild_id, None)
        if old_msg:
            try:
                await old_msg.delete()
            except Exception:
                pass
        await interaction.response.send_message(
            "𐙚˚⋆ หยุดและล้าง Queue แล้วนะ ♡", ephemeral=True
        )

    @discord.ui.button(emoji="📋", style=discord.ButtonStyle.secondary, row=0)
    async def show_queue_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        queue = get_queue(self.guild.id)
        if not queue:
            return await interaction.response.send_message(
                "📋 ˚⋆ ยังไม่มีเพลงเลยนะ ⋆˚", ephemeral=True
            )
        lines = [
            f"`{i}.` {s['title']} ({fmt_duration(s.get('duration'))})"
            for i, s in enumerate(list(queue)[:10], 1)
        ]
        if len(queue) > 10:
            lines.append(f"...และอีก {len(queue)-10} เพลง")
        total_dur = queue_total_duration(queue)
        lines.append(f"\n♡ รวม {len(queue)} เพลง • {total_dur}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class SearchView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, results: list[dict]):
        super().__init__(timeout=30)
        self.interaction = interaction
        self.results = results
        for i, song in enumerate(results[:5]):
            title_short = (
                song["title"][:50] + "…" if len(song["title"]) > 50 else song["title"]
            )
            btn = discord.ui.Button(
                label=f"{i+1}. {title_short}",
                style=discord.ButtonStyle.secondary,
                custom_id=f"search_{i}",
                row=i,
            )
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, index: int):
        async def callback(interaction: discord.Interaction):
            if interaction.user != self.interaction.user:
                return await interaction.response.send_message(
                    "˚⋆ ไม่ใช่คนค้นหาอะ ♡", ephemeral=True
                )
            # ขอเพลง = คนในห้องเสียงหรือ DJ (กันคนนอกห้องยัดเพลง)
            if not has_dj(interaction) and not _is_same_channel(interaction):
                return await interaction.response.send_message(
                    "˚⋆ เข้าห้องเสียงเดียวกับบอทก่อนนะ ♡", ephemeral=True
                )
            song = dict(self.results[index])
            song["requester"] = interaction.user.display_name
            guild = interaction.guild
            queue = get_queue(guild.id)
            queue.append(song)
            pos = len(queue)

            for item in self.children:
                item.disabled = True

            vc = guild.voice_client
            if vc and (vc.is_playing() or vc.is_paused()):
                await interaction.response.edit_message(
                    content=f"𐙚˚⋆ เพิ่ม **{song['title']}** เข้า Queue แล้ว ♡ อยู่ในคิวที่ #{pos}",
                    view=self,
                )
            else:
                await interaction.response.edit_message(
                    content=f"𐙚˚⋆ เพิ่ม **{song['title']}** เข้า Queue แล้วนะ ♡",
                    view=self,
                )
                if vc:
                    await _play_next_guild(guild, announce_channel=interaction.channel)

        return callback

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


# ── Core play logic ────────────────────────────────────────────────────────


# แก้เป็น
async def _play_next_guild(
    guild: discord.Guild,
    announce_channel: discord.abc.Messageable | None = None,
    announce: bool = True,
    keep_msg: bool = False,
) -> dict | None:
    guild_id = guild.id

    # จำ channel ไว้ใช้ต่อใน after_play
    if announce_channel:
        announce_channels[guild_id] = announce_channel
    elif guild_id in announce_channels:
        announce_channel = announce_channels[guild_id]

    queue = get_queue(guild_id)
    vc = guild.voice_client

    if not vc or not vc.is_connected():
        return None

    if not queue:
        now_playing.pop(guild_id, None)
        skip_votes.pop(guild_id, None)
        if not keep_msg:
            await _delete_now_playing_msg(guild_id)
        _reset_idle_timer(guild_id, vc)
        return None

    _cancel_idle_timer(guild_id)
    skip_votes.pop(guild_id, None)

    if not keep_msg:
        await _delete_now_playing_msg(guild_id)

    song = queue.popleft()
    now_playing[guild_id] = song

    logger.warning(f"[PLAY] โหลด: {song['title']}")

    file_id = f"{guild_id}_{int(time.time()*1000)}"
    tmp_base = f"/tmp/song_{file_id}"

    for ext in ["opus", "webm", "m4a", "mp3", "ogg", ""]:
        old = f"/tmp/song_{guild_id}.{ext}" if ext else f"/tmp/song_{guild_id}"
        try:
            if os.path.exists(old):
                os.remove(old)
        except Exception:
            pass

    try:
        ydl_opts = {
            "format": "bestaudio[ext=opus]/bestaudio[ext=webm]/bestaudio",
            "outtmpl": tmp_base,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "socket_timeout": 30,
        }

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([song["webpage_url"]])

        await asyncio.get_event_loop().run_in_executor(None, _download)

        actual_path = None
        for ext in ["opus", "webm", "m4a", "mp3", "ogg"]:
            p = f"{tmp_base}.{ext}"
            if os.path.exists(p):
                actual_path = p
                break
        if not actual_path and os.path.exists(tmp_base):
            actual_path = tmp_base

        if not actual_path:
            raise FileNotFoundError("ไม่พบไฟล์ที่ download")

        logger.warning(
            f"[PLAY] downloaded: {actual_path} ({os.path.getsize(actual_path)} bytes)"
        )

    except Exception as e:
        logger.warning(f"[PLAY] ❌ download error: {e}")
        if announce_channel:
            try:
                await announce_channel.send(
                    f"❌ โหลดเพลง **{song['title']}** ไม่ได้ ข้ามไปเพลงถัดไป...",
                    delete_after=5,
                )
            except Exception:
                pass
        return await _play_next_guild(
            guild, announce_channel=announce_channel, announce=announce
        )

    try:
        ffmpeg_audio = discord.FFmpegPCMAudio(actual_path, executable=FFMPEG_PATH)
        source = discord.PCMVolumeTransformer(ffmpeg_audio, volume=0.5)
    except Exception as e:
        logger.warning(f"[PLAY] ❌ FFmpegPCMAudio error: {e}")
        if announce_channel:
            try:
                await announce_channel.send(f"❌ FFmpeg error: {e}", delete_after=5)
            except Exception:
                pass
        return None

    def after_play(error):
        if error:
            logger.warning(f"[PLAY] ❌ after error: {error}")
        logger.warning(f"[PLAY] after_play — queue size: {len(get_queue(guild_id))}")
        try:
            if os.path.exists(actual_path):
                os.remove(actual_path)
        except Exception:
            pass
        asyncio.run_coroutine_threadsafe(
            _play_next_guild(guild, announce_channel=announce_channel, announce=True),
            bot.loop,
        )

    vc.play(source, after=after_play)
    logger.warning(f"[PLAY] is_playing={vc.is_playing()}")
    asyncio.create_task(_push_state(guild))

    logger.warning(f"[PLAY] announce={announce} channel={announce_channel}")
    if announce and announce_channel:
        sent = await announce_channel.send(
            embed=build_now_playing_embed(song, get_queue(guild_id), guild_id=guild_id),
            view=GuildPlayerView(guild),
        )
        now_playing_msg[guild_id] = sent
        logger.warning(f"[PLAY] ✅ ส่ง embed: {song['title']}")
    else:
        logger.warning(
            f"[PLAY] ❌ ไม่ส่ง embed — announce={announce} channel={announce_channel}"
        )

    gc.collect()
    return song


# ── Dashboard integration ──────────────────────────────────────────────────


async def _push_state(guild: discord.Guild):
    vc = guild.voice_client
    guild_id = guild.id
    payload = {
        "guild_id": str(guild_id),
        "guild_name": guild.name,
        "channel_name": vc.channel.name if vc and vc.channel else "",
        "is_playing": vc.is_playing() if vc else False,
        "is_paused": vc.is_paused() if vc else False,
        "volume": int(
            (vc.source.volume * 100) if (vc and hasattr(vc.source, "volume")) else 50
        ),
        "now_playing": now_playing.get(guild_id),
        "queue": list(get_queue(guild_id)),
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{DASHBOARD_URL}/update",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=2),
            )
    except Exception as e:
        logger.warning(f"[DASH] push failed: {e}")


async def _poll_dashboard():
    await bot.wait_until_ready()
    logger.warning(f"[DASH] เริ่ม poll {DASHBOARD_URL}")
    while not bot.is_closed():
        for guild in bot.guilds:
            guild_id = str(guild.id)
            try:
                async with aiohttp.ClientSession() as session:
                    r = await session.get(
                        f"{DASHBOARD_URL}/poll/{guild_id}",
                        timeout=aiohttp.ClientTimeout(total=2),
                    )
                    data = await r.json()
                    cmd = data.get("command")
                    if cmd:
                        await _handle_dashboard_cmd(guild, cmd, data)
            except Exception:
                pass
        await asyncio.sleep(1.0)


async def _handle_dashboard_cmd(guild: discord.Guild, cmd: str, data: dict):
    vc = guild.voice_client
    guild_id = guild.id

    if cmd == "skip":
        if vc:
            vc.stop()
    elif cmd == "pause":
        if vc and vc.is_playing():
            vc.pause()
    elif cmd == "resume":
        if vc and vc.is_paused():
            vc.resume()
    elif cmd == "stop":
        queues[guild_id] = deque()
        now_playing.pop(guild_id, None)
        skip_votes.pop(guild_id, None)
        if vc:
            vc.stop()
        await _delete_now_playing_msg(guild_id)
    elif cmd == "restart":
        current = now_playing.get(guild_id)
        if current and vc:
            get_queue(guild_id).appendleft(current)
            vc.stop()
    elif cmd == "volume":
        val = int(data.get("value", 50)) / 100
        if vc and hasattr(vc.source, "volume"):
            vc.source.volume = val
    elif cmd == "add_song":
        query = data.get("query", "")
        if query:
            sub = await _sub_status(guild_id)
            if not sub.get("ok", True):
                logger.warning(f"[BILL] guild {guild_id} expired — refuse dashboard add")
                return
            songs = await fetch_songs(query, limit=1)
            if songs:
                songs[0]["requester"] = "Dashboard"
                get_queue(guild_id).append(songs[0])
                vc = guild.voice_client
                if vc and not vc.is_playing() and not vc.is_paused():
                    await _play_next_guild(guild, announce_channel=None, announce=False)
    elif cmd == "remove_song":
        idx = int(data.get("index", 0))
        q = get_queue(guild_id)
        q_list = list(q)
        if 0 <= idx < len(q_list):
            q_list.pop(idx)
            queues[guild_id] = deque(q_list)

    await _push_state(guild)


# ── Events ─────────────────────────────────────────────────────────────────


@bot.event
async def on_ready():
    logger.warning(f"[READY] Bot: {bot.user.name} ({bot.user.id})")
    logger.warning(f"[READY] discord.py: {discord.__version__}")
    _ensure_opus()
    logger.warning(f"[READY] Opus loaded: {discord.opus.is_loaded()}")
    try:
        result = subprocess.run(
            [FFMPEG_PATH, "-version"], capture_output=True, text=True, timeout=5
        )
        first_line = (result.stdout or result.stderr).splitlines()[0]
        logger.warning(f"[DIAG] ffmpeg test: {first_line}")
    except Exception as e:
        logger.warning(f"[DIAG] ffmpeg test failed: {e}")

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening, name="˚⋆𐙚 /play <ชื่อเพลง> ♡"
        )
    )

    try:
        synced = await tree.sync()
        logger.warning(f"[READY] Synced {len(synced)} slash commands")
    except Exception as e:
        logger.warning(f"[READY] Sync failed: {e}")

    bot.loop.create_task(_poll_dashboard())
    bot.loop.create_task(_poll_billing())


@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    vc = member.guild.voice_client
    if not vc or not vc.channel:
        return
    human_members = [m for m in vc.channel.members if not m.bot]
    if len(human_members) == 0:
        logger.warning(
            f"[AUTO-DC] ไม่มีคนใน channel — เริ่มนับ {AUTO_DISCONNECT_DELAY}s"
        )
        guild_id = member.guild.id
        if guild_id in _idle_timers:
            _idle_timers[guild_id].cancel()
        _idle_timers[guild_id] = asyncio.create_task(_auto_disconnect(guild_id, vc))
    else:
        if vc.is_playing() or vc.is_paused():
            _cancel_idle_timer(member.guild.id)


# ── Slash Commands ─────────────────────────────────────────────────────────


@tree.command(name="join", description="เรียกบอทเข้า Voice Channel ♡")
async def join(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.voice:
        return await interaction.followup.send(
            "˚⋆ เข้า Voice Channel ก่อนนะ ♡", ephemeral=True
        )
    ch = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc:
        await vc.move_to(ch)
    else:
        await ch.connect(reconnect=True, self_deaf=False, self_mute=False)
    await interaction.followup.send(
        f"𐙚˚⋆ เข้าร่วม **{ch.name}** แล้วนะ ♡", ephemeral=True
    )


@tree.command(name="play", description="เล่นเพลงจาก YouTube ♡")
@app_commands.describe(query="ชื่อเพลงหรือ URL ที่ต้องการเล่น")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        return await interaction.followup.send(
            "˚⋆ เข้า Voice Channel ก่อนนะ ♡", ephemeral=True
        )

    guild = interaction.guild
    vc = guild.voice_client

    if vc and not _is_same_channel(interaction):
        target = interaction.user.voice.channel
        await interaction.followup.send(
            f"⚠️ บอทกำลังเล่นอยู่ใน **{vc.channel.name}** "
            f"— จะย้ายไปที่ **{target.name}** ไหม?",
            view=MoveChannelView(interaction, target, query),
        )
        return

    if not vc:
        vc = await interaction.user.voice.channel.connect(
            reconnect=True, self_deaf=False, self_mute=False
        )

    sub = await _sub_status(guild.id)
    blocked = _blocked_msg(sub)
    if blocked:
        return await interaction.followup.send(blocked, ephemeral=True)

    _cancel_idle_timer(guild.id)

    _is_radio = "list=RD" in query or "start_radio=1" in query
    is_playlist = (
        query.startswith("http")
        and not _is_radio
        and ("list=" in query or "/playlist" in query)
    )

    msg = await interaction.followup.send(f"⋆˚𐙚 กำลังหาเพลง **{query}** อยู่นะ ♡")

    try:
        if is_playlist:
            songs = await fetch_playlist_with_progress(query, msg, limit=50)
        else:
            songs = await fetch_songs(
                query, limit=1 if (not query.startswith("http") or _is_radio) else 50
            )
    except Exception as e:
        return await msg.edit(content=f"❌ เกิดข้อผิดพลาด: {e}")

    if not songs:
        return await msg.edit(content="˚⋆ หาเพลงไม่เจอเลย ♡ ลองใหม่นะ")

    queue = get_queue(guild.id)
    was_playing = vc.is_playing() or vc.is_paused() or guild.id in now_playing
    pos_before = len(queue)

    for s in songs:
        s["requester"] = interaction.user.display_name
        queue.append(s)

    if len(songs) > 1:
        await msg.edit(content=f"𐙚˚⋆ เพิ่ม **{len(songs)} เพลง** เข้า Queue แล้วนะ ♡")
        asyncio.create_task(_push_state(guild))
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
    elif was_playing:
        await msg.edit(
            content=f"✅ เพิ่ม **{songs[0]['title']}** ({fmt_duration(songs[0].get('duration'))}) "
            f"เข้า Queue แล้ว — อยู่ในคิวที่ #{pos_before + 1}"
        )
        asyncio.create_task(_push_state(guild))
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass
    else:
        played = await _play_next_guild(
            guild, announce_channel=interaction.channel, announce=False, keep_msg=True
        )
        if played:
            await msg.edit(
                content=None,
                embed=build_now_playing_embed(
                    played, get_queue(guild.id), guild_id=guild.id
                ),
                view=GuildPlayerView(guild),
            )
            now_playing_msg[guild.id] = msg
        return

    if not was_playing:
        await _play_next_guild(
            guild, announce_channel=interaction.channel, announce=False
        )


@tree.command(name="search", description="ค้นหาเพลงและเลือกจากผลลัพธ์ ♡")
@app_commands.describe(query="ชื่อเพลงที่ต้องการค้นหา")
async def search(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    vc = await _ensure_voice(interaction)
    if not vc:
        return

    msg = await interaction.followup.send(f"⋆˚𐙚 กำลังหาเพลง **{query}** อยู่นะ ♡")
    results = await fetch_songs(query, limit=5)
    if not results:
        return await msg.edit(content="˚⋆ หาเพลงไม่เจอเลย ♡ ลองใหม่นะ")

    lines = [
        f"`{i+1}.` **{r['title']}** ({fmt_duration(r.get('duration'))})"
        for i, r in enumerate(results)
    ]
    embed = discord.Embed(
        title=f"⋆˚ ผลค้นหา ˚⋆ • {query}", description="\n".join(lines), color=0x5865F2
    )
    embed.set_footer(text="♡ กดเลือกเพลงที่ชอบได้เลย ˚⋆ (หมดเวลา 30 วิ)")
    await msg.edit(content=None, embed=embed, view=SearchView(interaction, results))


@tree.command(name="pause", description="หยุดเพลงชั่วคราว ♡")
async def pause(interaction: discord.Interaction):
    if not _can_control(interaction):
        return await _deny(interaction)
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message(
            "𐙚˚⋆ หยุดชั่วคราวแล้วนะ ♡", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
        )


@tree.command(name="resume", description="เล่นเพลงต่อ ♡")
async def resume(interaction: discord.Interaction):
    if not _can_control(interaction):
        return await _deny(interaction)
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("𐙚˚⋆ เล่นต่อแล้วนะ ♡", ephemeral=True)
    else:
        await interaction.response.send_message(
            "˚⋆ ไม่มีเพลงที่หยุดอยู่เลย ♡", ephemeral=True
        )


@tree.command(name="skip", description="ข้ามเพลง (Vote skip ถ้ามีคนหลายคน) ♡")
async def skip(interaction: discord.Interaction):
    if not _can_control(interaction):
        return await _deny(interaction)
    await interaction.response.defer()
    vc = interaction.guild.voice_client
    if not vc or (not vc.is_playing() and not vc.is_paused()):
        return await interaction.followup.send(
            "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
        )

    guild_id = interaction.guild.id
    current = now_playing.get(guild_id, {})
    requester = current.get("requester", "")

    if interaction.user.display_name == requester:
        skip_votes.pop(guild_id, None)
        vc.stop()
        return await interaction.followup.send("𐙚˚⋆ ข้ามเพลงแล้วนะ ♡", ephemeral=True)

    human_members = [m for m in vc.channel.members if not m.bot]
    needed = max(VOTE_SKIP_THRESHOLD, len(human_members) // 2 + 1)

    if len(human_members) < VOTE_SKIP_THRESHOLD:
        skip_votes.pop(guild_id, None)
        vc.stop()
        return await interaction.followup.send("𐙚˚⋆ ข้ามเพลงแล้วนะ ♡", ephemeral=True)

    votes = skip_votes.setdefault(guild_id, set())
    votes.add(interaction.user.id)

    if len(votes) >= needed:
        skip_votes.pop(guild_id, None)
        vc.stop()
        await interaction.followup.send(
            f"⏭ Vote skip ผ่าน ({len(votes)}/{needed}) — ข้ามเพลงแล้ว!"
        )
    else:
        await interaction.followup.send(
            f"𐙚˚⋆ โหวตข้ามเพลง **{len(votes)}/{needed}** โหวต"
        )


@tree.command(name="stop", description="หยุดเพลงและล้าง Queue ♡")
async def stop(interaction: discord.Interaction):
    if not has_dj(interaction):
        return await _deny(interaction, "˚⋆ ปุ่มนี้เฉพาะ DJ นะ ♡")
    guild_id = interaction.guild.id
    queues[guild_id] = deque()
    now_playing.pop(guild_id, None)
    skip_votes.pop(guild_id, None)
    announce_channels.pop(guild_id, None)
    vc = interaction.guild.voice_client
    if vc:
        vc.stop()
    await _delete_now_playing_msg(guild_id)
    _reset_idle_timer(guild_id, vc)
    await interaction.response.send_message(
        "𐙚˚⋆ หยุดเล่นและล้าง Queue แล้วนะ ♡", ephemeral=True
    )


@tree.command(name="queue", description="ดู Queue เพลงทั้งหมด ♡")
async def show_queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = get_queue(guild_id)
    embed = discord.Embed(title="⋆˚𐙚 Queue เพลง 𐙚˚⋆", color=0x5865F2)

    if guild_id in now_playing:
        s = now_playing[guild_id]
        embed.add_field(
            name="𐙚 กำลังเล่น",
            value=f"**{s['title']}** ({fmt_duration(s.get('duration'))}) — {s.get('requester','?')}",
            inline=False,
        )
    if not queue:
        embed.add_field(
            name="˚⋆ ยังไม่มีเพลงเลยนะ ⋆˚",
            value="ลอง `/play` เพื่อเพิ่มเพลงได้เลย ♡",
            inline=False,
        )
    else:
        lines = [
            f"`{i}.` **{s['title']}** ({fmt_duration(s.get('duration'))}) — {s.get('requester','?')}"
            for i, s in enumerate(list(queue)[:10], 1)
        ]
        if len(queue) > 10:
            lines.append(f"...และอีก {len(queue)-10} เพลง")
        total_dur = queue_total_duration(queue)
        embed.add_field(
            name=f"รายการถัดไป ({len(queue)} เพลง • รวม {total_dur})",
            value="\n".join(lines),
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="nowplaying", description="ดูเพลงที่กำลังเล่นอยู่ ♡")
async def now_playing_cmd(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    if guild_id not in now_playing:
        return await interaction.response.send_message(
            "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
        )
    await interaction.response.send_message(
        embed=build_now_playing_embed(
            now_playing[guild_id], get_queue(guild_id), guild_id=guild_id
        ),
        view=GuildPlayerView(interaction.guild),
    )


@tree.command(name="clear", description="ล้าง Queue ทั้งหมด ♡")
async def clear_queue(interaction: discord.Interaction):
    if not has_dj(interaction):
        return await _deny(interaction, "˚⋆ ปุ่มนี้เฉพาะ DJ นะ ♡")
    queues[interaction.guild.id] = deque()
    await interaction.response.send_message("𐙚˚⋆ ล้าง Queue แล้วนะ ♡", ephemeral=True)


@tree.command(name="leave", description="ไล่บอทออกจาก Voice Channel ♡")
async def leave(interaction: discord.Interaction):
    if not has_dj(interaction):
        return await _deny(interaction, "˚⋆ ปุ่มนี้เฉพาะ DJ นะ ♡")
    vc = interaction.guild.voice_client
    if vc:
        _cancel_idle_timer(interaction.guild.id)
        await _delete_now_playing_msg(interaction.guild.id)
        queues.pop(interaction.guild.id, None)
        now_playing.pop(interaction.guild.id, None)
        skip_votes.pop(interaction.guild.id, None)
        announce_channels.pop(interaction.guild.id, None)
        await vc.disconnect()
        await interaction.response.send_message("𐙚˚⋆ บ๊ายบาย ♡", ephemeral=True)
    else:
        await interaction.response.send_message(
            "˚⋆ บอทไม่ได้อยู่ใน Voice Channel นะ ♡", ephemeral=True
        )


@tree.command(name="volume", description="ปรับระดับเสียง 0-100 ♡")
@app_commands.describe(vol="ระดับเสียง (0-100)")
async def volume(interaction: discord.Interaction, vol: int):
    if not has_dj(interaction):
        return await _deny(interaction, "˚⋆ ปุ่มนี้เฉพาะ DJ นะ ♡")
    vc = interaction.guild.voice_client
    if not vc or not vc.is_playing():
        return await interaction.response.send_message(
            "˚⋆ ยังไม่มีเพลงเล่นอยู่นะ ♡", ephemeral=True
        )
    if not 0 <= vol <= 100:
        return await interaction.response.send_message(
            "˚⋆ ใส่ตัวเลข 0-100 นะ ♡", ephemeral=True
        )
    if hasattr(vc.source, "volume"):
        vc.source.volume = vol / 100
        await interaction.response.send_message(
            f"𐙚˚⋆ ปรับเสียงเป็น **{vol}%** แล้วนะ ♡", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            "˚⋆ ปรับเสียงในโหมดนี้ไม่ได้นะ ♡", ephemeral=True
        )


@tree.command(name="dashboard", description="ขอลิงก์ Dashboard ของเซิร์ฟเวอร์นี้ ♡")
async def dashboard(interaction: discord.Interaction):
    dj = has_dj(interaction)
    key = await _fetch_dashboard_key(interaction.guild.id) if dj else None
    await interaction.response.send_message(
        _dashboard_messages(interaction.guild.id, key, dj), ephemeral=True
    )


@tree.command(name="dashboard-key", description="รีเซ็ตลิงก์ Dashboard (แอดมินเท่านั้น) ♡")
async def dashboard_key(interaction: discord.Interaction):
    if not is_admin(interaction):
        return await _deny(interaction, "˚⋆ เฉพาะแอดมินดิสเท่านั้นนะ ♡")
    await interaction.response.send_message(
        await _dashboard_key_msg(interaction), ephemeral=True
    )


async def _dashboard_key_msg(interaction: discord.Interaction) -> str:
    key = await _rotate_dashboard_key(interaction.guild.id)
    if not key:
        return "❌ ต่อ backend ไม่ได้ ลองใหม่นะ"
    return f"[🖥️ ลิงก์ Dashboard ใหม่]({_dashboard_link(interaction.guild.id, key)}) — ลิงก์เก่าใช้ไม่ได้แล้วนะ ♡"


# ── Billing ────────────────────────────────────────────────────────────────


def _is_owner(interaction: discord.Interaction) -> bool:
    return OWNER_DISCORD_ID and interaction.user.id == OWNER_DISCORD_ID


class SlipApproveView(discord.ui.View):
    """ปุ่ม ✅/❌ ในห้องแอดมิน — กดได้เฉพาะเจ้าของบอท (OWNER_DISCORD_ID)"""

    def __init__(self, pending_id: str, guild_id: int):
        super().__init__(timeout=None)
        self.pending_id = pending_id
        self.guild_id = guild_id

    async def _decide(
        self, interaction: discord.Interaction, approve: bool
    ):
        if not _is_owner(interaction):
            return await interaction.response.send_message(
                "˚⋆ เฉพาะเจ้าของบอทนะ ♡", ephemeral=True
            )
        action = "approve" if approve else "reject"
        ok = False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{DASHBOARD_URL}/internal/billing/{action}",
                    headers={"X-Bot-Secret": BOT_SECRET},
                    json={"pending_id": self.pending_id},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    ok = r.status == 200
        except Exception as e:
            logger.warning(f"[BILL] {action} failed: {e}")
        for item in self.children:
            item.disabled = True
        if ok:
            txt = (
                "✅ อนุมัติแล้ว ต่ออายุ 30 วัน ♡"
                if approve
                else "❌ ตีกลับสลิปนี้แล้ว"
            )
        else:
            txt = "❌ ต่อ backend ไม่ได้ ลองใหม่นะ"
        try:
            await interaction.response.edit_message(content=txt, view=self)
        except Exception:
            pass

    @discord.ui.button(label="✅ อนุมัติ 30 วัน", style=discord.ButtonStyle.success)
    async def approve_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._decide(interaction, True)

    @discord.ui.button(label="❌ ตีกลับ", style=discord.ButtonStyle.danger)
    async def deny_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await self._decide(interaction, False)


async def _send_pending_slip(pending: dict):
    """ส่งสลิปค้างตรวจเข้าห้องแอดมินพร้อมปุ่มอนุมัติ"""
    if not ADMIN_CHANNEL_ID:
        logger.warning("[BILL] no ADMIN_DISCORD_CHANNEL_ID — skip slip notify")
        return False
    channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(ADMIN_CHANNEL_ID)
        except Exception as e:
            logger.warning(f"[BILL] admin channel not found: {e}")
            return False
    guild_id = pending.get("guild_id", "?")
    g = bot.get_guild(int(guild_id)) if str(guild_id).isdigit() else None
    gname = g.name if g else pending.get("guild_name") or f"Guild {guild_id}"
    img_bytes = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{DASHBOARD_URL}/internal/slip/{pending['pending_id']}",
                headers={"X-Bot-Secret": BOT_SECRET},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    img_bytes = await r.read()
    except Exception as e:
        logger.warning(f"[BILL] slip download failed: {e}")

    embed = discord.Embed(
        title="🧾 สลิปใหม่รอตรวจ",
        description=(
            f"ดิส: **{gname}** (`{guild_id}`)\n"
            f"ส่งเมื่อ: {pending.get('created_at', '?')}\n"
            "กด ✅ = ต่ออายุ 30 วัน / ❌ = ตีกลับ"
        ),
        color=0xF5C518,
    )
    files = []
    if img_bytes:
        files.append(discord.File(io.BytesIO(img_bytes), filename="slip.png"))
        embed.set_image(url="attachment://slip.png")
    try:
        await channel.send(
            embed=embed,
            files=files,
            view=SlipApproveView(pending["pending_id"], int(guild_id))
            if str(guild_id).isdigit()
            else None,
        )
    except Exception as e:
        logger.warning(f"[BILL] admin send failed: {e}")
        return False
    # mark notified กันส่งซ้ำหลังรีสตาร์ท
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{DASHBOARD_URL}/internal/pending/{pending['pending_id']}/notified",
                headers={"X-Bot-Secret": BOT_SECRET},
                timeout=aiohttp.ClientTimeout(total=5),
            )
    except Exception:
        pass
    return True


async def _poll_billing():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{DASHBOARD_URL}/internal/pending",
                    headers={"X-Bot-Secret": BOT_SECRET},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    if r.status == 200:
                        for pending in (await r.json()).get("pending", []):
                            await _send_pending_slip(pending)
        except Exception:
            pass
        await asyncio.sleep(15)


@tree.command(name="subscription", description="ดูสถานะแพ็กเกจของเซิร์ฟเวอร์นี้ ♡")
async def subscription(interaction: discord.Interaction):
    sub = await _sub_status(interaction.guild.id)
    if sub.get("trial"):
        msg = f"🎁 ช่วงทดลองใช้เหลืออีก **{sub.get('trial_left', '?')} วัน** ♡ ถูกใจค่อยต่อ 99฿/เดือนที่ {PRICING_URL}"
    elif sub.get("paid"):
        msg = f"✅ แพ็กเกจใช้งานได้ถึง **{sub.get('paid_until') or '?'}** ♡"
    elif sub.get("in_grace"):
        msg = f"⚠️ หมดอายุ {sub.get('paid_until')} แต่ยังฟังได้ช่วงผ่อนผัน รีบต่อที่ {PRICING_URL} นะ ♡"
    else:
        msg = _blocked_msg(sub) or "❌ แพ็กเกจใช้ไม่ได้"
    await interaction.response.send_message(msg, ephemeral=True)


@tree.command(name="invite", description="ขอลิงก์เชิญบอทไปดิสอื่น ♡")
async def invite(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"[🤖 เชิญไอแว่นเข้าดิสของคุณ]({invite_url()})\n"
        "ติดตั้งแล้วสร้าง role `DJ` ให้คนที่คุมเพลงได้เลยนะ ♡ "
        f"ดูแพ็กเกจที่ {PRICING_URL}",
        ephemeral=True,
    )


@tree.command(name="billing-pending", description="ดูสลิปค้างตรวจอีกครั้ง (เจ้าของบอท) ♡")
async def billing_pending(interaction: discord.Interaction):
    if not _is_owner(interaction):
        return await _deny(interaction, "˚⋆ เฉพาะเจ้าของบอทนะ ♡")
    items = []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{DASHBOARD_URL}/internal/pending",
                headers={"X-Bot-Secret": BOT_SECRET},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as r:
                if r.status == 200:
                    items = (await r.json()).get("pending", [])
    except Exception:
        pass
    if not items:
        return await interaction.response.send_message(
            "✅ ไม่มีสลิปค้างตรวจ ♡", ephemeral=True
        )
    await interaction.response.send_message(
        f"🧾 มี {len(items)} สลิปค้าง — กำลังส่งเข้าห้องแอดมินนะ", ephemeral=True
    )
    for pending in items:
        await _send_pending_slip(pending)


if __name__ == "__main__":
    bot.run(TOKEN)
