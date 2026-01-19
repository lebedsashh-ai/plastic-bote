import os
import re
import sqlite3
from datetime import datetime
from urllib.parse import quote_plus

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes,
    ConversationHandler, filters
)

DB_PATH = "plastic.db"
SPOOL_DEFAULT_GRAMS = 1000

# --- Состояния ---
ADD_BRAND, ADD_TYPE, ADD_COLOR = range(3)
SUBTRACT_GRAMS = 10

# --- Режимы (чтобы не путать “выбор катушки” и “быстрое добавление”) ---
MODE_KEY = "mode"
MODE_ADD_QUICK = "add_quick"
MODE_NONE = None

# --- Regex ---
RE_SPOOL_PICK = re.compile(r"^\s*(\d+)\.\s+")  # "1. Brand Type Color — 1000 г"

# ------------------ DB ------------------
def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS spools (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand TEXT NOT NULL,
            ptype TEXT NOT NULL,
            color TEXT NOT NULL,
            remaining INTEGER NOT NULL,
            archived INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spool_id INTEGER NOT NULL,
            grams INTEGER NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Для выпадающих списков брендов/типов/цветов
    c.execute("""
        CREATE TABLE IF NOT EXISTS dict_values (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,     -- 'brand' | 'ptype' | 'color'
            value TEXT NOT NULL,
            UNIQUE(kind, value)
        )
    """)

    conn.commit()
    conn.close()

def dict_add(kind: str, value: str):
    value = value.strip()
    if not value:
        return
    conn = db()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO dict_values(kind, value) VALUES(?,?)", (kind, value))
    conn.commit()
    conn.close()

def dict_list(kind: str, limit: int = 20):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT value FROM dict_values WHERE kind=? ORDER BY value COLLATE NOCASE LIMIT ?", (kind, limit))
    rows = [r[0] for r in c.fetchall()]
    conn.close()
    return rows

def add_spool(brand: str, ptype: str, color: str):
    brand, ptype, color = brand.strip(), ptype.strip(), color.strip()
    conn = db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO spools(brand, ptype, color, remaining, archived) VALUES(?,?,?,?,0)",
        (brand, ptype, color, SPOOL_DEFAULT_GRAMS)
    )
    conn.commit()
    conn.close()

    dict_add("brand", brand)
    dict_add("ptype", ptype)
    dict_add("color", color)

def get_spools(active_only=True):
    conn = db()
    c = conn.cursor()
    if active_only:
        c.execute("SELECT id, brand, ptype, color, remaining FROM spools WHERE archived=0 ORDER BY id DESC")
    else:
        c.execute("SELECT id, brand, ptype, color, remaining, archived FROM spools ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def get_spool(spool_id: int):
    conn = db()
    c = conn.cursor()
    c.execute("SELECT id, brand, ptype, color, remaining, archived FROM spools WHERE id=?", (spool_id,))
    row = c.fetchone()
    conn.close()
    return row

def subtract_grams(spool_id: int, grams: int, note: str | None):
    conn = db()
    c = conn.cursor()

    c.execute("SELECT remaining FROM spools WHERE id=?", (spool_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        raise ValueError("Катушка не найдена")

    remaining = row[0]
    new_remaining = remaining - grams
    if new_remaining < 0:
        conn.close()
        raise ValueError(f"Нельзя списать {grams} г — осталось только {remaining} г")

    c.execute("UPDATE spools SET remaining=? WHERE id=?", (new_remaining, spool_id))
    c.execute(
        "INSERT INTO history(spool_id, grams, note, created_at) VALUES(?,?,?,?)",
        (spool_id, grams, note, datetime.now().isoformat(timespec="seconds"))
    )

    # автоархив если почти пусто
    if new_remaining <= 10:
        c.execute("UPDATE spools SET archived=1 WHERE id=?", (spool_id,))

    conn.commit()
    conn.close()
    return new_remaining

def archive_spool(spool_id: int):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE spools SET archived=1 WHERE id=?", (spool_id,))
    conn.commit()
    conn.close()

def unarchive_spool(spool_id: int):
    conn = db()
    c = conn.cursor()
    c.execute("UPDATE spools SET archived=0 WHERE id=?", (spool_id,))
    conn.commit()
    conn.close()

def get_history(spool_id: int, limit: int = 20):
    conn = db()
    c = conn.cursor()
    c.execute(
        "SELECT grams, note, created_at FROM history WHERE spool_id=? ORDER BY id DESC LIMIT ?",
        (spool_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return rows

# ------------------ UI ------------------
def kb_main():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📦 Мой пластик"), KeyboardButton("➕ Добавить катушку")],
            [KeyboardButton("🔍 Поиск"), KeyboardButton("📁 Архив")],
            [KeyboardButton("ℹ Помощь")],
        ],
        resize_keyboard=True
    )

def kb_spools(spools):
    rows = []
    for sid, brand, ptype, color, remaining in spools:
        rows.append([KeyboardButton(f"{sid}. {brand} {ptype} {color} — {remaining} г")])
    rows.append([KeyboardButton("⬅ Назад")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def kb_spool_actions():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➖ Списать граммы"), KeyboardButton("📜 История")],
            [KeyboardButton("ℹ Инфо"), KeyboardButton("🛒 Купить")],
            [KeyboardButton("📁 В архив"), KeyboardButton("⬅ Назад")],
        ],
        resize_keyboard=True
    )

def kb_pick_from_list(values, extra_buttons=None):
    rows = [[KeyboardButton(v)] for v in values]
    if extra_buttons:
        for b in extra_buttons:
            rows.append([KeyboardButton(b)])
    rows.append([KeyboardButton("⬅ Назад")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def make_search_links(brand, ptype, color):
    q = f"{brand} {ptype} {color} 1.75 filament"
    qq = quote_plus(q)
    # Стабильно: просто поисковые ссылки (потом сделаем парсинг магазинов)
    return [
        ("🔎 Google", f"https://www.google.com/search?q={qq}"),
        ("🛒 Ozon", f"https://www.ozon.ru/search/?text={qq}"),
        ("🛒 Wildberries", f"https://www.wildberries.ru/catalog/0/search.aspx?search={qq}"),
        ("🛒 AliExpress", f"https://www.aliexpress.com/wholesale?SearchText={qq}"),
    ]
# ------------------ Команды ------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[MODE_KEY] = MODE_NONE
    await update.message.reply_text(
        "Привет! Я помогу вести склад пластика.\n"
        "Добавляй катушки, списывай граммы и смотри историю.",
        reply_markup=kb_main()
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команды:\n"
        "• /start — главное меню\n"
        "• /master — пошаговое добавление катушки\n\n"
        "Как пользоваться:\n"
        "➕ Добавить катушку — быстрый ввод одной строкой или /master\n"
        "📦 Мой пластик — выбирай катушку и списывай граммы\n"
        "Списание: можно '250' или '250 корпус'.",
        reply_markup=kb_main()
    )

# ------------------ Добавление (мастер) ------------------
async def add_master_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[MODE_KEY] = MODE_NONE
    brands = dict_list("brand", 12)
    if brands:
        await update.message.reply_text(
            "Выбери бренд из списка или введи новый:",
            reply_markup=kb_pick_from_list(brands, extra_buttons=["✍️ Ввести новый бренд"])
        )
    else:
        await update.message.reply_text("Введи бренд пластика:")
    return ADD_BRAND

async def add_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if t == "⬅ Назад":
        await update.message.reply_text("Главное меню", reply_markup=kb_main())
        return ConversationHandler.END

    if t == "✍️ Ввести новый бренд":
        await update.message.reply_text("Ок, введи новый бренд:")
        return ADD_BRAND

    context.user_data["brand"] = t
    dict_add("brand", t)

    types_ = dict_list("ptype", 12)
    if types_:
        await update.message.reply_text(
            "Выбери тип из списка или введи новый:",
            reply_markup=kb_pick_from_list(types_, extra_buttons=["✍️ Ввести новый тип"])
        )
    else:
        await update.message.reply_text("Введи тип (PLA / PETG / ABS / TPU ...):")
    return ADD_TYPE

async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if t == "⬅ Назад":
        await update.message.reply_text("Главное меню", reply_markup=kb_main())
        return ConversationHandler.END

    if t == "✍️ Ввести новый тип":
        await update.message.reply_text("Ок, введи новый тип:")
        return ADD_TYPE

    context.user_data["ptype"] = t
    dict_add("ptype", t)

    colors = dict_list("color", 12)
    if colors:
        await update.message.reply_text(
            "Выбери цвет из списка или введи новый:",
            reply_markup=kb_pick_from_list(colors, extra_buttons=["✍️ Ввести новый цвет"])
        )
    else:
        await update.message.reply_text("Введи цвет:")
    return ADD_COLOR

async def add_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if t == "⬅ Назад":
        await update.message.reply_text("Главное меню", reply_markup=kb_main())
        return ConversationHandler.END

    if t == "✍️ Ввести новый цвет":
        await update.message.reply_text("Ок, введи новый цвет:")
        return ADD_COLOR

    brand = context.user_data.get("brand")
    ptype = context.user_data.get("ptype")
    color = t

    if not (brand and ptype and color):
        await update.message.reply_text("Что-то пошло не так. Начни заново: /master", reply_markup=kb_main())
        return ConversationHandler.END

    add_spool(brand, ptype, color)
    context.user_data[MODE_KEY] = MODE_NONE

    await update.message.reply_text(
        f"✅ Добавлена катушка:\n{brand} {ptype} {color} — {SPOOL_DEFAULT_GRAMS} г",
        reply_markup=kb_main()
    )
    return ConversationHandler.END

# ------------------ Быстрое добавление ------------------
async def add_quick_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Включаем режим “быстро добавить”
    context.user_data[MODE_KEY] = MODE_ADD_QUICK
    await update.message.reply_text(
        "Введи одной строкой:\n"
        "Бренд Тип Цвет\n\n"
        "Пример:\n"
        "eSUN PLA+ Красный\n\n"
        "Или напиши /master для пошагового.",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⬅ Назад")]], resize_keyboard=True)
    )

def parse_quick_line(line: str):
    parts = line.strip().split()
    if len(parts) < 3:
        return None
    brand = parts[0]
    ptype = parts[1]
    color = " ".join(parts[2:])
    return brand, ptype, color
# ------------------ Просмотр катушек ------------------
async def show_my_spools(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data[MODE_KEY] = MODE_NONE
    spools = get_spools(active_only=True)
    if not spools:
        await update.message.reply_text("Список пуст. Добавь катушку.", reply_markup=kb_main())
        return
    await update.message.reply_text("Выбери катушку:", reply_markup=kb_spools(spools))

async def pick_spool_from_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка выбора катушки вида:
    '1. eSUN PLA Красный — 900 г'
    """
    m = RE_SPOOL_PICK.match(update.message.text or "")
    if not m:
        return False
    spool_id = int(m.group(1))
    spool = get_spool(spool_id)
    if not spool or spool[5] == 1:
        await update.message.reply_text("Катушка не найдена (возможно в архиве).", reply_markup=kb_main())
        return True

    context.user_data["current_spool_id"] = spool_id
    _, brand, ptype, color, remaining, _arch = spool
    await update.message.reply_text(
        f"📦 {brand} {ptype} {color}\nОсталось: {remaining} г",
        reply_markup=kb_spool_actions()
    )
    return True

# ------------------ Списание ------------------
async def subtract_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_spool_id")
    if not sid:
        await update.message.reply_text("Сначала выбери катушку в 📦 Мой пластик.", reply_markup=kb_main())
        return ConversationHandler.END

    await update.message.reply_text(
        "Введи граммы. Можно с комментом:\n"
        "• 250\n"
        "• 250 корпус",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⬅ Назад")]], resize_keyboard=True)
    )
    return SUBTRACT_GRAMS

async def subtract_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t == "⬅ Назад":
        await update.message.reply_text("Ок", reply_markup=kb_spool_actions())
        return ConversationHandler.END

    parts = t.split(maxsplit=1)
    try:
        grams = int(parts[0])
    except:
        await update.message.reply_text("Нужно число граммов, например: 250")
        return SUBTRACT_GRAMS

    if grams <= 0:
        await update.message.reply_text("Граммы должны быть > 0")
        return SUBTRACT_GRAMS

    note = parts[1] if len(parts) > 1 else None
    sid = context.user_data.get("current_spool_id")

    try:
        new_remaining = subtract_grams(sid, grams, note)
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")
        return SUBTRACT_GRAMS

    spool = get_spool(sid)
    _, brand, ptype, color, _rem, archived = spool
    if archived == 1:
        await update.message.reply_text(
            f"✅ Списано {grams} г. Осталось {new_remaining} г.\n"
            "Катушка почти пустая — отправил в архив.",
            reply_markup=kb_main()
        )
    else:
        await update.message.reply_text(
            f"✅ Списано {grams} г. Осталось {new_remaining} г.",
            reply_markup=kb_spool_actions()
        )
    return ConversationHandler.END

# ------------------ История ------------------
async def show_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_spool_id")
    if not sid:
        await update.message.reply_text("Сначала выбери катушку.", reply_markup=kb_main())
        return

    rows = get_history(sid, 20)
    if not rows:
        await update.message.reply_text("История пуста.", reply_markup=kb_spool_actions())
        return

    text = "📜 Последние списания:\n"
    for grams, note, dt in rows:
        line = f"{dt}: -{grams} г"
        if note:
            line += f" — {note}"
        text += line + "\n"

    await update.message.reply_text(text, reply_markup=kb_spool_actions())

# ------------------ Архив ------------------
async def archive_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_spool_id")
    if not sid:
        await update.message.reply_text("Сначала выбери катушку.", reply_markup=kb_main())
        return
    archive_spool(sid)
    await update.message.reply_text("Катушка отправлена в архив.", reply_markup=kb_main())

async def show_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_spools(active_only=False)
    archived = [r for r in rows if r[5] == 1]
    if not archived:
        await update.message.reply_text("Архив пуст.", reply_markup=kb_main())
        return

    text = "📁 Архив:\n"
    for sid, brand, ptype, color, remaining, _arch in archived:
        text += f"{sid}. {brand} {ptype} {color} — {remaining} г\n"
    text += "\nЧтобы вернуть катушку — напиши: /unarchive ID\nНапример: /unarchive 12"

    await update.message.reply_text(text, reply_markup=kb_main())

async def cmd_unarchive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = (update.message.text or "").split()
    if len(args) != 2 or not args[1].isdigit():
        await update.message.reply_text("Формат: /unarchive ID\nНапример: /unarchive 12", reply_markup=kb_main())
        return
    sid = int(args[1])
    unarchive_spool(sid)
    await update.message.reply_text(f"Катушка {sid} возвращена из архива.", reply_markup=kb_main())

# ------------------ Инфо / Купить / Поиск ------------------
async def show_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_spool_id")
    if not sid:
        await update.message.reply_text("Сначала выбери катушку.", reply_markup=kb_main())
        return
    spool = get_spool(sid)
    _, brand, ptype, color, remaining, _arch = spool
    links = make_search_links(brand, ptype, color)

    msg = (
        f"ℹ Информация (пока через поиск):\n"
        f"{brand} {ptype} {color}\n"
        f"Осталось: {remaining} г\n\n"
        f"Ссылки:\n" +
        "\n".join([f"{name}: {url}" for name, url in links[:1]])  # 1 ссылка на общий поиск
    )
    await update.message.reply_text(msg, reply_markup=kb_spool_actions())

async def show_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sid = context.user_data.get("current_spool_id")
    if not sid:
        await update.message.reply_text("Сначала выбери катушку.", reply_markup=kb_main())
        return
    spool = get_spool(sid)
    _, brand, ptype, color, _remaining, _arch = spool
    links = make_search_links(brand, ptype, color)

    msg = "🛒 Где купить (поиск по магазинам):\n" + "\n".join([f"{name}: {url}" for name, url in links[1:]])
    await update.message.reply_text(msg, reply_markup=kb_spool_actions())

async def search_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 Поиск по складу:\n"
        "Напиши слово, например: PLA или Красный или eSUN",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("⬅ Назад")]], resize_keyboard=True)
    )
    context.user_data["await_search"] = True

async def search_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t == "⬅ Назад":
        context.user_data["await_search"] = False
        await update.message.reply_text("Главное меню", reply_markup=kb_main())
        return

    context.user_data["await_search"] = False
    q = t.lower()
    rows = get_spools(active_only=True)
    found = []
    for sid, brand, ptype, color, remaining in rows:
        if q in brand.lower() or q in ptype.lower() or q in color.lower():
            found.append((sid, brand, ptype, color, remaining))

    if not found:
        await update.message.reply_text("Ничего не нашёл.", reply_markup=kb_main())
        return

    await update.message.reply_text("Нашёл:", reply_markup=kb_spools(found))
# ------------------ Главный роутер (важно: порядок условий!) ------------------
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # 0) Если ждём ввод для поиска
    if context.user_data.get("await_search"):
        return await search_do(update, context)

    # 1) Выбор катушки (должно быть РАНЬШЕ, чем быстрый ввод)
    if RE_SPOOL_PICK.match(text):
        handled = await pick_spool_from_text(update, context)
        if handled:
            return

    # 2) Кнопки меню
    if text == "📦 Мой пластик":
        return await show_my_spools(update, context)

    if text == "➕ Добавить катушку":
        return await add_quick_hint(update, context)

    if text == "🔍 Поиск":
        return await search_hint(update, context)

    if text == "📁 Архив":
        return await show_archive(update, context)

    if text == "ℹ Помощь":
        return await cmd_help(update, context)

    # 3) Кнопки внутри катушки
    if text == "➖ Списать граммы":
        # запускается ConversationHandler, но на всякий случай:
        return await subtract_start(update, context)

    if text == "📜 История":
        return await show_history(update, context)

    if text == "ℹ Инфо":
        return await show_info(update, context)

    if text == "🛒 Купить":
        return await show_buy(update, context)

    if text == "📁 В архив":
        return await archive_current(update, context)

    if text == "⬅ Назад":
        context.user_data[MODE_KEY] = MODE_NONE
        await update.message.reply_text("Главное меню", reply_markup=kb_main())
        return

    # 4) Быстрое добавление — ТОЛЬКО если мы в режиме add_quick
    if context.user_data.get(MODE_KEY) == MODE_ADD_QUICK:
        parsed = parse_quick_line(text)
        if not parsed:
            await update.message.reply_text("Формат: Бренд Тип Цвет (минимум 3 слова). Попробуй ещё раз.")
            return
        brand, ptype, color = parsed
        add_spool(brand, ptype, color)
        context.user_data[MODE_KEY] = MODE_NONE
        await update.message.reply_text(
            f"✅ Добавлена катушка:\n{brand} {ptype} {color} — {SPOOL_DEFAULT_GRAMS} г",
            reply_markup=kb_main()
        )
        return

    # 5) Если ничего не распознали
    await update.message.reply_text(
        "Не понял. Используй меню или /help",
        reply_markup=kb_main()
    )

# ------------------ main ------------------
def main():
    init_db()
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Не задана переменная окружения BOT_TOKEN (Render → Environment Variables)")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("master", add_master_start))
    app.add_handler(CommandHandler("unarchive", cmd_unarchive))

    # Пошаговый мастер
    master = ConversationHandler(
        entry_points=[CommandHandler("master", add_master_start)],
        states={
            ADD_BRAND: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_brand)],
            ADD_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_type)],
            ADD_COLOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_color)],
        },
        fallbacks=[],
    )
    app.add_handler(master)

    # Списание (диалог)
    subtract_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➖ Списать граммы$"), subtract_start)],
        states={SUBTRACT_GRAMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, subtract_do)]},
        fallbacks=[],
    )
    app.add_handler(subtract_conv)

    # Роутер
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))

    app.run_polling()

if __name__ == "__main__":
    main()
