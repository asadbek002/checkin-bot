import logging
import asyncio
import nest_asyncio
import os
from math import radians, cos, sin, asin, sqrt
from datetime import datetime, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    filters, ConversationHandler
)
from notion_client import Client

# === Настройки ===
NOTION_TOKEN = os.environ['NOTION_TOKEN']
DATABASE_ID = os.environ['NOTION_DATABASE']
BOT_TOKEN = os.environ['BOT_TOKEN']
OFFICE_LAT = 41.0057953
OFFICE_LON = 71.6804896
GEO_RADIUS_METERS = 100
ASK_REASON = 1
ADMIN_CHAT_ID = 5897615611  # при необходимости замени

notion = Client(auth=NOTION_TOKEN)

# === Логирование ===
logging.basicConfig(level=logging.INFO)

# === Гео-проверка ===
def is_in_office(user_lat, user_lon):
    def haversine(lat1, lon1, lat2, lon2):
        lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
        c = 2 * asin(sqrt(a))
        return 6371000 * c
    return haversine(user_lat, user_lon, OFFICE_LAT, OFFICE_LON) <= GEO_RADIUS_METERS

# === Сохранение в Notion ===
def save_to_notion(user_id, user_name, status, reason=""):
    now = datetime.utcnow() + timedelta(hours=5)
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "Check-in": {"title": [{"text": {"content": f"{user_name} - {status}"}}]},
            "Telegram ID": {"rich_text": [{"text": {"content": str(user_id)}}]},
            "Ism": {"rich_text": [{"text": {"content": user_name}}]},
            "Sana": {"date": {"start": now.strftime("%Y-%m-%d")}},
            "Vaqt": {"rich_text": [{"text": {"content": now.strftime("%H:%M")}}]},
            "Holat": {"select": {"name": status}},
            "Joy": {"rich_text": [{"text": {"content": "Ofisda"}}]},
            "Sabab": {"rich_text": [{"text": {"content": reason}}]}
        }
    )

# === Подсчёт опозданий ===
def get_late_count(user_id):
    today = datetime.utcnow() + timedelta(hours=5)
    month_str = today.strftime("%Y-%m")
    results = notion.databases.query(
        **{
            "database_id": DATABASE_ID,
            "filter": {
                "and": [
                    {"property": "Telegram ID", "rich_text": {"contains": str(user_id)}},
                    {"property": "Holat", "select": {"equals": "Kelgan"}},
                    {"property": "Sabab", "rich_text": {"is_not_empty": True}},
                    {"property": "Sana", "date": {"on_or_after": f"{month_str}-01"}}
                ]
            }
        }
    )
    return len(results.get("results", []))

# === Хендлеры ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    keyboard = [[KeyboardButton("✅ Kelish", request_location=True)],
                [KeyboardButton("❌ Ketish")]]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(
        f"Salom, {user.first_name}!\n"
        f"Ishga kelganligingizni tasdiqlash uchun 'Kelish' tugmasini bosing va joylashuvingizni yuboring:",
        reply_markup=markup
    )

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    loc = update.message.location
    if not loc:
        await update.message.reply_text("🚫 Iltimos, lokatsiyani yuboring.")
        return ConversationHandler.END
    if not is_in_office(loc.latitude, loc.longitude):
        await update.message.reply_text("⚠️ Siz ofis yaqinida emassiz. Iltimos, ofis hududida belgilang.")
        return ConversationHandler.END

    now_kor = datetime.utcnow() + timedelta(hours=5)
    is_late = now_kor.hour > 9 or (now_kor.hour == 9 and now_kor.minute > 0)

    if is_late:
        if get_late_count(user.id) >= 3:
            await update.message.reply_text("❌ Bu oyda 3 martadan ortiq kechikdingiz.")
            save_to_notion(user.id, user.first_name, "Kelgan", "Sababsiz kech qoldi (blok)")
            return ConversationHandler.END
        else:
            context.user_data['entry'] = (user.id, user.first_name, "Kelgan")
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"⚠️ {user.first_name} ({user.id}) kechikdi. Sababini kutyapmiz."
            )
            await update.message.reply_text("⏰ Siz ishga kech qoldingiz. Iltimos, sababni yozing:")
            return ASK_REASON

    save_to_notion(user.id, user.first_name, "Kelgan")
    await update.message.reply_text("✅ Ofisda ekanligingiz tasdiqlandi. Xush kelibsiz!")
    return ConversationHandler.END

async def reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sabab = update.message.text
    entry = context.user_data.get('entry')
    if entry:
        user_id, name, status = entry
        save_to_notion(user_id, name, status, sabab)
        await update.message.reply_text("✅ Sabab qabul qilindi va ishga kelish qayd etildi.")
    return ConversationHandler.END

async def ketish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_to_notion(user.id, user.first_name, "Ketgan")
    await update.message.reply_text("👋 Xayr! Ketish vaqtingiz qayd etildi.")

# === Запуск ===
async def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.LOCATION, location_handler)],
        states={ASK_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, reason_handler)]},
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^❌ Ketish$"), ketish))

    print("✅ Bot ishlamoqda...")
    await app.run_polling()

# === Безопасный запуск ===
async def main():
    await run_bot()

nest_asyncio.apply()
try:
    loop = asyncio.get_event_loop()
    loop.create_task(main())
    loop.run_forever()
except Exception as e:
    print("❌ Ошибка запуска:", e)
