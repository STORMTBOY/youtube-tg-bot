import os, re, asyncio, aiofiles
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import yt_dlp

# 🔑 ENV VARS
TOKEN = os.environ["BOT_TOKEN"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "change-me")
BASE_URL = os.environ.get("RENDER_EXTERNAL_URL")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook")

COOKIE_FILE = os.environ.get("YOUTUBE_COOKIES", "cookies.txt")
POPULAR_HEIGHTS = [1080, 720, 480, 360, 240]

app = FastAPI()
application = Application.builder().token(TOKEN).build()
YOUTUBE_RE = re.compile(r'(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+)', re.I)

tmpdir = Path("/tmp")
tmpdir.mkdir(exist_ok=True)

# ───── Conversation States ─────
CHOOSING_FORMAT = 1
MAX_SIZE_MB = 50

# ───── دستورات بات ─────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام! لینک یوتیوب بده تا کیفیت‌های محبوب و سالم رو برات نشون بدم 🎬")
    return CHOOSING_FORMAT

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    m = YOUTUBE_RE.search(text)
    if not m:
        await update.message.reply_text("❌ لینک یوتیوب معتبر نیست.")
        return CHOOSING_FORMAT
    url = m.group(1)
    await update.message.reply_text("درحال بررسی کیفیت‌ها و حجم‌های تقریبی... ⏳")

    ydl_opts = {"quiet": True, "no_warnings": True}
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        ydl_opts["cookiefile"] = COOKIE_FILE

    formats_map = {}
    msg_lines = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get("formats", [])

        i = 1
        added = set()
        for h in POPULAR_HEIGHTS:
            candidates = [f for f in formats if f.get("vcodec") != "none" and f.get("height") == h]
            if not candidates:
                continue
            f = max(candidates, key=lambda x: x.get("tbr", 0))
            # بهترین audio
            best_audio = None
            for fa in formats:
                if fa.get("acodec") != "none" and fa.get("vcodec") == "none":
                    best_audio = fa
                    break
            if f.get("acodec") != "none":
                fmt = f['format_id']
                total_size = (f.get("filesize") or 0)/ (1024*1024)
            else:
                fmt = f"{f['format_id']}+{best_audio['format_id'] if best_audio else 'bestaudio'}"
                vsize = f.get("filesize") or 0
                asize = best_audio.get("filesize") if best_audio else 0
                total_size = (vsize + (asize or 0)) / (1024*1024)

            if total_size > MAX_SIZE_MB:  # فقط فرمت‌های ≤50MB
                continue
            if fmt in added:
                continue
            added.add(fmt)
            msg_lines.append(f"{i}: {f.get('height','?')}p, ~{round(total_size,1)} MB")
            formats_map[str(i)] = fmt
            i += 1

    if not msg_lines:
        await update.message.reply_text(f"❌ هیچ کیفیتی ≤ {MAX_SIZE_MB}MB پیدا نشد.")
        return CHOOSING_FORMAT

    await update.message.reply_text("\n".join(msg_lines)[:4000])
    await update.message.reply_text("یک عدد (شماره کیفیت) انتخاب کن:")

    context.user_data["yt_url"] = url
    context.user_data["formats_map"] = formats_map
    return CHOOSING_FORMAT

async def handle_format(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text.strip()
    url = context.user_data.get("yt_url")
    formats_map = context.user_data.get("formats_map", {})

    if not url or choice not in formats_map:
        await update.message.reply_text("❌ لطفاً یک شماره معتبر از لیست فرمت‌ها انتخاب کن.")
        return CHOOSING_FORMAT

    format_id = formats_map[choice]
    await update.message.reply_text(f"دانلود کیفیت انتخابی ({choice}) شروع شد... ⏳")

    ydl_opts = {
        "format": format_id,
        "outtmpl": str(tmpdir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    if COOKIE_FILE and os.path.exists(COOKIE_FILE):
        ydl_opts["cookiefile"] = COOKIE_FILE

    file_path = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)
            if not file_path.endswith(".mp4"):
                file_path = str(Path(file_path).with_suffix(".mp4"))

        await update.message.reply_text("دانلود تمام شد، در حال آپلود... ⏳")

        await update.message.reply_video(video=InputFile(file_path),
                                        caption=info.get("title", "")[:1024],
                                        supports_streaming=True)

    except Exception as e:
        await update.message.reply_text(f"❌ خطا در دانلود/ارسال: {e}")
    finally:
        try:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)
        except:
            pass

    return ConversationHandler.END

# ───── هندلر Conversation ─────
conv_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_cmd)],
    states={
        CHOOSING_FORMAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg),
                          MessageHandler(filters.TEXT & ~filters.COMMAND, handle_format)]
    },
    fallbacks=[]
)
application.add_handler(conv_handler)

# ───── FastAPI ─────
@app.get("/")
async def health():
    return {"status": "ok"}

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
    await application.bot.set_webhook(url=BASE_URL + WEBHOOK_PATH,
                                      secret_token=WEBHOOK_SECRET,
                                      drop_pending_updates=True,
                                      allowed_updates=["message"])

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()
