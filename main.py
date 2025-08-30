import os, re, asyncio, aiofiles, shutil
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import math
import subprocess

TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook")
COOKIE_FILE = os.environ.get("YOUTUBE_COOKIES")  # خواندن مسیر از Env Var
if COOKIE_FILE:
    ydl_opts["cookiefile"] = COOKIE_FILE
    
app = FastAPI()
application = Application.builder().token(TOKEN).build()

YOUTUBE_RE = re.compile(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+)', re.I)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! لینک یوتیوب بده تا کیفیت‌ها و حجم‌ها رو برات نشون بدم 🎬")

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = YOUTUBE_RE.search(text)
    if not m:
        return
    url = m.group(1)
    await update.message.reply_text("درحال بررسی کیفیت‌ها و حجم‌ها... ⏳")

    ydl_opts = {"quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get("formats", [])
        msg_lines = []
        for f in formats:
            if f.get("vcodec") != "none":
                size_mb = (f.get("filesize") or 0) / (1024*1024)
                msg_lines.append(f"{f['format_id']}: {f.get('height', '?')}p, {f.get('ext')}, ~{round(size_mb,1)} MB")
        if not msg_lines:
            await update.message.reply_text("❌ کیفیتی پیدا نشد.")
            return
        await update.message.reply_text("\n".join(msg_lines)[:4000])

    await update.message.reply_text("لطفا format_id دلخواهت رو بفرست تا دانلود کنم:")

    # ذخیره url برای مرحله بعد
    context.user_data["yt_url"] = url

async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    format_id = update.message.text.strip()
    url = context.user_data.get("yt_url")
    if not url:
        await update.message.reply_text("❌ ابتدا لینک یوتیوب بده.")
        return

    await update.message.reply_text(f"دانلود با format_id={format_id} شروع شد... ⏳")
    tmpdir = Path("/tmp")
    tmpdir.mkdir(exist_ok=True)

    ydl_opts = {
    "format": "bestvideo+bestaudio/best",
    "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
    "merge_output_format": "mp4",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "cookiefile": "cookies.txt",  # ← مسیر فایل cookies.txt
}

    file_path = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            if not file_path.endswith(".mp4"):
                file_path = os.path.splitext(file_path)[0] + ".mp4"

        # بررسی اندازه و تقسیم خودکار
        max_size = 50*1024*1024  # 50MB
        file_size = os.path.getsize(file_path)
        if file_size <= max_size:
            async with aiofiles.open(file_path, "rb") as f:
                await update.message.reply_video(video=InputFile(file_path),
                                                caption=info.get("title","")[:1024],
                                                supports_streaming=True)
        else:
            # تقسیم فایل با ffmpeg
            num_parts = math.ceil(file_size / max_size)
            part_pattern = str(tmpdir / "part%03d.mp4")
            subprocess.run([
                "ffmpeg","-i",file_path,"-c","copy","-map","0",
                "-f","segment","-segment_size", str(max_size),
                part_pattern
            ])
            # ارسال هر قسمت
            for i in range(num_parts):
                part_file = tmpdir / f"part{i:03d}.mp4"
                if part_file.exists():
                    async with aiofiles.open(part_file,"rb") as f:
                        await update.message.reply_video(video=InputFile(part_file),
                                                        caption=f"{info.get('title','')} (Part {i+1})",
                                                        supports_streaming=True)
                    os.remove(part_file)

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در دانلود/ارسال: {e}")
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

application.add_handler(CommandHandler("start", start_cmd))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
application.add_handler(MessageHandler(filters.Regex(r'^\w+$'), handle_format))  # format_id

# سلامت
@app.get("/")
async def health():
    return {"status": "ok"}

# وبهوک
class TelegramUpdate(BaseModel):
    update_id: int | None = None

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="forbidden")
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    if not BASE_URL:
        raise RuntimeError("RENDER_EXTERNAL_URL not set")
    await application.bot.set_webhook(
        url=BASE_URL + WEBHOOK_PATH,
        secret_token=WEBHOOK_SECRET,
        drop_pending_updates=True,
        allowed_updates=["message"]
    )

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()



