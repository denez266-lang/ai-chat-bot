import os, base64, logging, collections, asyncio, time
import httpx
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]
GKEY = os.environ["GEMINI_API_KEY"]
MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS", "gemini-3.5-flash-lite,gemini-3.6-flash,gemini-3.8-flash").split(",") if m.strip()]
SYSTEM = ("أنت مساعد ذكاء اصطناعي داخل تلكرام. جاوب بنفس لغة المستخدم، وإذا كتب بالعربي "
          "جاوب بلهجة عراقية بسيطة وواضحة. اكتب، ترجم، لخّص، اشرح، وافهم الصور. "
          "لا تدّعي أن عندك وصول لإيميل أو تقويم أو حسابات. كن مختصراً ما لم يُطلب التفصيل.")
history = collections.defaultdict(list)  # chat_id -> list of {role, parts}; lost on restart
MAXTURNS = 20


async def ask(chat_id, parts):
    h = history[chat_id]
    h.append({"role": "user", "parts": parts})
    body = {"system_instruction": {"parts": [{"text": SYSTEM}]}, "contents": h[-MAXTURNS:]}
    r = None
    t0 = time.time()
    async with httpx.AsyncClient(timeout=60) as c:
        for attempt in range(6):
            model = MODELS[attempt % len(MODELS)]
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            try:
                r = await c.post(url, headers={"x-goog-api-key": GKEY}, json=body)
            except Exception as e:
                logging.error("gemini request failed %s", e)
                r = None
                await asyncio.sleep(1.5)
                continue
            if r.status_code == 200:
                logging.info("gemini ok model=%s attempt=%s secs=%.1f", model, attempt, time.time() - t0)
                break
            logging.error("gemini %s %s -> %s %s", model, attempt, r.status_code, r.text[:200])
            if r.status_code in (429, 500, 503, 404):
                await asyncio.sleep(1.5)
                continue
            break
    if r is None or r.status_code != 200:
        h.pop()
        return "الذكاء الاصطناعي مشغول هسه، جرب مرة ثانية بعد شوية."
    try:
        text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"]).strip()
    except Exception:
        h.pop()
        return "ما كدرت أجاوب على هذا، جرب صياغة ثانية."
    h.append({"role": "model", "parts": [{"text": text}]})
    del h[:-MAXTURNS]
    return text or "..."


async def send(update, text):
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i + 4000])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    history.pop(update.effective_chat.id, None)
    await update.message.reply_text("هلا! أنا بوت ذكاء اصطناعي. اسألني أي شي، اكتب، ترجم، أو ابعث صورة وأشرحها لك.\n/new لبدء محادثة جديدة.")


async def new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    history.pop(update.effective_chat.id, None)
    await update.message.reply_text("تمام، بدأنا محادثة جديدة.")


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    await send(update, await ask(update.effective_chat.id, [{"text": update.message.text}]))


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    f = await update.message.photo[-1].get_file()
    data = bytes(await f.download_as_bytearray())
    parts = [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(data).decode()}},
             {"text": update.message.caption or "اشرح هذه الصورة."}]
    r = await ask(update.effective_chat.id, parts)
    # keep history light: replace the image with a note
    for m in history[update.effective_chat.id]:
        m["parts"] = [p if "inline_data" not in p else {"text": "[صورة]"} for p in m["parts"]]
    await send(update, r)


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if url:
        port = int(os.environ.get("PORT", 10000))
        app.run_webhook(listen="0.0.0.0", port=port, url_path=TOKEN, webhook_url=f"{url}/{TOKEN}")
    else:
        app.run_polling()


if __name__ == "__main__":
    main()
