import logging
import asyncio
import nest_asyncio
import os
import json
from math import radians, cos, sin, asin, sqrt
from datetime import datetime, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    filters, ConversationHandler
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pandas as pd

# === Настройки ===
GOOGLE_SHEET_ID = '1YavT3ZyVdPu5SxuHTyjgqeDeyTSxShpaAMevz9f061M'
OFFICE_LAT = 41.0057953
OFFICE_LON = 71.6804896
GEO_RADIUS_METERS = 100
ASK_REASON = 1

# === Google Sheets (через ENV с JSON) ===
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
google_creds = json.loads(os.environ['GOOGLE_CREDENTIALS'])
credentials = ServiceAccountCredentials.from_json_keyfile_dict(google_creds, scope)
gs = gspread.authorize(credentials)
worksheet = gs.open_by_key(GOOGLE_SHEET_ID).sheet1

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

# === Подсчёт опозданий ===
def get_late_count(user_id):
    records = worksheet.get_all_records()
    df = pd.DataFrame(records)
    df.columns = df.columns.str.strip()
    if df.empty:
        return 0
    df['Sana'] = pd.to_datetime(df['Sana'], errors='coerce')
    df['Oy'] = df['Sana'].dt.strftime("%Y-%m")
    now_str = (datetime.utcnow() + timedelta(hours=5)).strftime("%Y-%m")
    filtered = df[
        (df['Telegram ID'].astype(str) == str(user_id)) &
        (df['Holat'] == 'Kelgan') &
        (df['Sabab'].notnull()) &
        (df['Sabab'] != '') &
        (df['Oy'] == now_str)
    ]
    return len(filtered)

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
            worksheet.append_row([str(user.id), user.first_name, now_kor.strftime("%Y-%m-%d"),
                                  now_kor.strftime("%H:%M"), "Kelgan", "Ofisda", "Sababsiz kech qoldi (blok)"])
            return ConversationHandler.END
        else:
            context.user_data['entry'] = [str(user.id), user.first_name, now_kor.strftime("%Y-%m-%d"),
                                          now_kor.strftime("%H:%M"), "Kelgan", "Ofisda"]
            await context.bot.send_message(
                chat_id=5897615611,
                text=f"⚠️ {user.first_name} ({user.id}) kechikdi. Sababini kutyapmiz."
            )
            await update.message.reply_text("⏰ Siz ishga kech qoldingiz. Iltimos, sababni yozing:")
            return ASK_REASON
    worksheet.append_row([str(user.id), user.first_name, now_kor.strftime("%Y-%m-%d"),
                          now_kor.strftime("%H:%M"), "Kelgan", "Ofisda", ""])
    await update.message.reply_text("✅ Ofisda ekanligingiz tasdiqlandi. Xush kelibsiz!")
    return ConversationHandler.END

async def reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sabab = update.message.text
    entry = context.user_data.get('entry', [])
    if entry:
        entry.append(sabab)
        worksheet.append_row(entry)
        await update.message.reply_text("✅ Sabab qabul qilindi va ishga kelish qayd etildi.")
    return ConversationHandler.END

async def ketish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    now_kor = datetime.utcnow() + timedelta(hours=5)
    worksheet.append_row([
        str(user.id), user.first_name, now_kor.strftime("%Y-%m-%d"),
        now_kor.strftime("%H:%M"), "Ketgan", "Noma'lum", ""
    ])
    await update.message.reply_text("👋 Xayr! Ketish vaqtingiz qayd etildi.")

async def tarix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    records = worksheet.get_all_records()
    if not records:
        await update.message.reply_text("🗂 Hech qanday yozuv topilmadi.")
        return
    df = pd.DataFrame(records)
    df.columns = df.columns.str.strip()
    df = df[df['Telegram ID'].astype(str) == str(user.id)]
    if args:
        try:
            target_date = args[0]
            df = df[df['Sana'] == target_date]
        except:
            await update.message.reply_text("⚠️ Sana noto‘g‘ri formatda. Masalan: /tarix 2025-06-01")
            return
    else:
        df = df.sort_values(by='Sana', ascending=False).head(5)
    if df.empty:
        await update.message.reply_text("📭 Ko‘rsatilgan sanaga oid yozuvlar topilmadi.")
    else:
        result = ""
        for _, row in df.iterrows():
            sabab = row['Sabab'] if 'Sabab' in row and row['Sabab'] else "–"
            result += f"{row['Sana']} {row['Vaqt']} — {row['Holat']} ({row['Joy']}) — {sabab}\n"
        await update.message.reply_text(result)

# === Запуск ===
async def run_bot():
    app = ApplicationBuilder().token(os.environ['BOT_TOKEN']).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.LOCATION, location_handler)],
        states={ASK_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, reason_handler)]},
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("tarix", tarix))
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
