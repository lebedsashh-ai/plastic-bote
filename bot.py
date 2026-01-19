import os
import sqlite3
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    ConversationHandler, filters
)

# ---------- Настройки ----------
DB_PATH = "plastic.db"
SPOOL_GRAMS = 1000

# Состояния диалогов
ADD_BRAND, ADD_TYPE, ADD_COLOR, ADD_QUICK = range(4)
SUBTRACT_GRAMS = 10

# ---------- База данных ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS spools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT,
            ptype TEXT,
            color TEXT,
            remaining INTEGER,
            archived INTEGER DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spool_id INTEGER,
            grams INTEGER,
            note TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def add_spool(brand, ptype, color):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO spools (brand, ptype, color, remaining) VALUES (?,?,?,?)",
        (brand, ptype, color, SPOOL_GRAMS)
    )
    conn.commit()
    conn.close()

def get_spools(active_only=True):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if active_only:
        c.execute("SELECT id, brand, ptype, color, remaining FROM spools WHERE archived=0")
    else:
        c.execute("SELECT id, brand, ptype, color, remaining FROM spools")
    rows = c.fetchall()
    conn.close()
    return rows

def get_spool(spool_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, brand, ptype, color, remaining FROM spools WHERE id=?", (spool_id,))
    row = c.fetchone()
    conn.close()
    return row

def subtract_grams(spool_id, grams, note=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE spools SET remaining = remaining - ? WHERE id=?", (grams, spool_id))
    c.execute(
        "INSERT INTO history (spool_id, grams, note, created_at) VALUES (?,?,?,?)",
        (spool_id, grams, note, datetime.now().isoformat(timespec="seconds"))
    )
    conn.commit()
    conn.close()

def archive_spool(spool_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE spools SET archived=1 WHERE id=?", (spool_id,))
    conn.commit()
    conn.close()

def get_history(spool_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT grams, note, created_at FROM history WHERE spool_id=? ORDER BY id DESC LIMIT 20",
        (spool_id,)
    )
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- Клавиатуры ----------
def main_menu_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📦 Мой пластик"), KeyboardButton("➕ Добавить катушку")],
            [KeyboardButton("🔍 Поиск"), KeyboardButton("📁 Архив")],
            [KeyboardButton("ℹ Помощь")]
        ],
        resize_keyboard=True
    )

def spools_kb(spools):
    buttons = [[KeyboardButton(f"{s[0]}. {s[1]} {s[2]} {s[3]} — {s[4]} г")] for s in spools]
    buttons.append([KeyboardButton("⬅ Назад")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def spool_actions_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➖ Списать граммы"), KeyboardButton("📜 История")],
            [KeyboardButton("📁 В архив"), KeyboardButton("⬅ Назад")]
        ],
        resize_keyboard=True
    )

# ---------- Хэндлеры ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Это склад пластика для 3D-печати.\n"
        "Добавляй катушки, списывай граммы и веди историю.",
        reply_markup=main_menu_kb()
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Как пользоваться:\n"
        "➕ Добавить катушку — мастер добавления (бренд → тип → цвет)\n"
        "📦 Мой пластик — список катушек, выбор и списание\n"
        "В списании можно ввести число или: `250 корпус`",
        reply_markup=main_menu_kb()
    )

# ---------- Добавление катушки (мастер) ----------
async def add_spool_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введи бренд пластика:")
    return ADD_BRAND

async def add_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["brand"] = update.message.text.strip()
    await update.message.reply_text("Введи тип пластика (PLA, PETG, ABS и т.д.):")
    return ADD_TYPE

async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ptype"] = update.message.text.strip()
    await update.message.reply_text("Введи цвет:")
    return ADD_COLOR

async def add_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    color = update.message.text.strip()
    brand = context.user_data["brand"]
    ptype = context.user_data["ptype"]
    add_spool(brand, ptype, color)
    await update.message.reply_text(
        f"Готово! Добавлена катушка:\n{brand} {ptype} {color} — {SPOOL_GRAMS} г",
        reply_markup=main_menu_kb()
    )
    return ConversationHandler.END

# Быстрое добавление одной строкой: "eSUN PLA+ Красный"
async def add_quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split()
    if len(parts) < 3:
        await update.message.reply_text("Формат: Бренд Тип Цвет (минимум 3 слова)")
        return ConversationHandler.END
    brand = parts[0]
    ptype = parts[1]
    color = " ".join(parts[2:])
    add_spool(brand, ptype, color)
    await update.message.reply_text(
        f"Быстро добавлено: {brand} {ptype} {color} — {SPOOL_GRAMS} г",
        reply_markup=main_menu_kb()
    )
    return ConversationHandler.END

# ---------- Мой пластик ----------
async def my_spools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    spools = get_spools(active_only=True)
    if not spools:
        await update.message.reply_text("Список пуст. Добавь катушку.", reply_markup=main_menu_kb())
        return
    await update.message.reply_text("Выбери катушку:", reply_markup=spools_kb(spools))

async def select_spool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "⬅ Назад":
        await update.message.reply_text("Главное меню", reply_markup=main_menu_kb())
        return
    try:
        spool_id = int(text.split(".")[0])
    except:
        return
    context.user_data["current_spool_id"] = spool_id
    spool = get_spool(spool_id)
    if not spool:
        await update.message.reply_text("Катушка не найдена.", reply_markup=main_menu_kb())
        return
    _, brand, ptype, color, remaining = spool
    await update.message.reply_text(
        f"{brand} {ptype} {color}\nОсталось: {remaining} г",
        reply_markup=spool_actions_kb()
    )

# ---------- Списание ----------
async def subtract_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введи граммы (можно с комментарием: `250 корпус`):")
    return SUBTRACT_GRAMS

async def subtract_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    parts = text.split(maxsplit=1)
    try:
        grams = int(parts[0])
    except:
        await update.message.reply_text("Нужно ввести число граммов.")
        return SUBTRACT_GRAMS

    note = parts[1] if len(parts) > 1 else None
    spool_id = context.user_data.get("current_spool_id")
    subtract_grams(spool_id, grams, note)

    spool = get_spool(spool_id)
    _, brand, ptype, color, remaining = spool

    # автоархив при остатке <=10 г
    if remaining <= 10:
        archive_spool(spool_id)
        msg = f"Списано {grams} г. Осталось {remaining} г.\nКатушка отправлена в архив (почти пустая)."
    else:
        msg = f"Списано {grams} г. Осталось {remaining} г."

    await update.message.reply_text(msg, reply_markup=spool_actions_kb())
    return ConversationHandler.END

# ---------- История / Архив ----------
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    spool_id = context.user_data.get("current_spool_id")
    rows = get_history(spool_id)
    if not rows:
        await update.message.reply_text("История пуста.", reply_markup=spool_actions_kb())
        return
    text = "Последние списания:\n"
    for grams, note, dt in rows:
        line = f"{dt}: -{grams} г"
        if note:
            line += f" — {note}"
        text += line + "\n"
    await update.message.reply_text(text, reply_markup=spool_actions_kb())

async def archive_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    spool_id = context.user_data.get("current_spool_id")
    archive_spool(spool_id)
    await update.message.reply_text("Катушка отправлена в архив.", reply_markup=main_menu_kb())

async def show_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    spools = get_spools(active_only=False)
    archived = [s for s in spools if s[4] <= 10]
    if not archived:
        await update.message.reply_text("Архив пуст.", reply_markup=main_menu_kb())
        return
    text = "Архив (почти пустые):\n"
    for s in archived:
        text += f"{s[1]} {s[2]} {s[3]} — {s[4]} г\n"
    await update.message.reply_text(text, reply_markup=main_menu_kb())

# ---------- Роутинг сообщений ----------
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text

    if t == "📦 Мой пластик":
        return await my_spools(update, context)
    if t == "➕ Добавить катушку":
        await update.message.reply_text("Введи одной строкой: Бренд Тип Цвет\nИли напиши /master для пошагового.")
        return
    if t == "📁 Архив":
        return await show_archive(update, context)
    if t == "ℹ Помощь":
        return await help_cmd(update, context)
    if t == "➖ Списать граммы":
        return await subtract_start(update, context)
    if t == "📜 История":
        return await show_history(update, context)
    if t == "📁 В архив":
        return await archive_current(update, context)
    if t == "⬅ Назад":
        await update.message.reply_text("Главное меню", reply_markup=main_menu_kb())
        return

    # Быстрое добавление одной строкой
    if len(t.split()) >= 3:
        return await add_quick(update, context)

    # Выбор катушки из списка
    if "." in t and t.split(".")[0].isdigit():
        return await select_spool(update, context)

# ---------- main ----------
def main():
    init_db()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN")

    app = Application.builder().token(token).build()

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # Мастер добавления
    master = ConversationHandler(
        entry_points=[CommandHandler("master", add_spool_start)],
        states={
            ADD_BRAND: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_brand)],
            ADD_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_type)],
            ADD_COLOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_color)],
        },
        fallbacks=[],
    )
    app.add_handler(master)

    # Списание
    subtract_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➖ Списать граммы$"), subtract_start)],
        states={SUBTRACT_GRAMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, subtract_do)]},
        fallbacks=[],
    )
    app.add_handler(subtract_conv)

    # Роутер
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

    # Запуск бота
    app.run_polling()

if __name__ == "__main__":
    main()
