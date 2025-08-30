import os, asyncio, aiofiles, subprocess
from pathlib import Path
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import math

# ۱️⃣ این مقادیر را در Render → Env Vars ست کن
TOKEN = os.environ["BOT_TOKEN"]              # توکن بات تلگرام از BotFather
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook")  # مسیر امن وبهوک
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")  # رشته امن دلخواه

application = Application.builder().token(TOKEN).build()

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! لینک یوتیوب بده تا دانلود 480p شروع شود 🎬")

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    url = text
    if not url.startswith("http"):
        await update.message.reply_text("لینک نامعتبر است ❌")
        return

    await update.message.reply_text("درحال دانلود و آماده‌سازی ویدیو... ⏳")
    tmpdir = Path("/tmp")
    tmpdir.mkdir(exist_ok=True)

    ydl_opts = {
        "format": "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
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
            num_parts = math.ceil(file_size / max_size)
            part_pattern = str(tmpdir / "part%03d.mp4")
            subprocess.run([
                "ffmpeg","-i",file_path,"-c","copy","-map","0",
                "-f","segment","-segment_size", str(max_size),
                part_pattern
            ])
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
