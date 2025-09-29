import aiosqlite
import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    MessageHandler,
    filters,
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

logging.basicConfig(level=logging.INFO)

DB_FILE = "tasks.db"
def load_token(path="token.txt"):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()
# ---------- DB helpers (async) (оставляем как у тебя) ----------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                task TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user_id);")
        await db.commit()

async def add_task_db(user_id: str, task: str) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        cur = await db.execute(
            "INSERT INTO tasks (user_id, task) VALUES (?, ?)", (user_id, task)
        )
        await db.commit()
        return cur.lastrowid

async def get_tasks_db(user_id: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL;")
        cur = await db.execute(
            "SELECT id, task FROM tasks WHERE user_id = ? ORDER BY created_at", (user_id,)
        )
        rows = await cur.fetchall()
        return rows

async def delete_task_by_index(user_id: str, index: int):
    rows = await get_tasks_db(user_id)
    if 0 <= index < len(rows):
        row = rows[index]
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute(
                "DELETE FROM tasks WHERE id = ? AND user_id = ?", (row['id'], user_id)
            )
            await db.commit()
        return True, row['task']
    return False, None

# ---------- UI helpers (menu / keyboards) ----------
MAIN_MENU_TEXT = "Привет! Я бот для списка задач. Выбирай действие:"
def main_menu_markup():
    kb = [
        [InlineKeyboardButton("Добавить задачу", callback_data="add")],
        [InlineKeyboardButton("Показать задачи", callback_data="list")],
        [InlineKeyboardButton("Удалить задачу", callback_data="delete")],
        [InlineKeyboardButton("Помощь", callback_data="help")],
    ]
    return InlineKeyboardMarkup(kb)

def delete_keyboard(rows):
    kb = [[InlineKeyboardButton(f"❌ {i+1}. {row['task']}", callback_data=f"del_{i}")]
          for i, row in enumerate(rows)]
    kb.append([InlineKeyboardButton("↩ Назад", callback_data="back")])
    return InlineKeyboardMarkup(kb)

def list_with_back_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton("↩ Назад", callback_data="back")]])

# ---------- State storage ----------
user_state = {}  # user_id -> state string (e.g. "ADDING_TASK")
# store last menu message to attempt editing instead of sending new messages:
user_last_menu_msg = {}  # user_id -> (chat_id, message_id)

# ---------- Handlers ----------
async def show_main_menu_and_store(bot, user_id: str, chat_id: int, message_id: int | None):
    """Try to edit existing menu message; if fail — send new one. Return (chat_id, message_id)."""
    text = MAIN_MENU_TEXT
    markup = main_menu_markup()
    try:
        if message_id is not None:
            # try to edit the message (preferred)
            await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
            return (chat_id, message_id)
        else:
            raise ValueError("no message_id -> send new")
    except Exception:
        # fallback: send new message and save it
        msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        return (chat_id, msg.message_id)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    # send main menu and save message id for that user
    msg = await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_markup())
    user_last_menu_msg[user_id] = (chat_id, msg.message_id)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /help command — single grouped message
    text = (
        "📖 Помощь:\n"
        "1️⃣ /add – добавить задачу\n"
        "2️⃣ /list – показать список задач\n"
        "3️⃣ Удалить задачу – через меню"
    )
    await update.message.reply_text(text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # обязательно отвечаем на callback
    user_id = str(query.from_user.id)
    chat_id = query.message.chat_id
    last = user_last_menu_msg.get(user_id)
    last_chat_id, last_message_id = (last if last else (chat_id, None))

    if query.data == "add":
        # prompt for a new task — edit menu to the prompt so user sees one message
        try:
            await query.edit_message_text("Напиши задачу, и я сохраню её ✅")
            # save menu message location so we can edit it after task added
            user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
        except Exception:
            await query.message.reply_text("Напиши задачу, и я сохраню её ✅")
        user_state[user_id] = "ADDING_TASK"

    elif query.data == "list":
        rows = await get_tasks_db(user_id)
        if not rows:
            # edit menu message to show "пусто" and keep main menu keyboard (or back)
            try:
                await query.edit_message_text("У тебя пока нет задач 😅", reply_markup=main_menu_markup())
                user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
            except Exception:
                await query.message.reply_text("У тебя пока нет задач 😅")
        else:
            text = "📝 Твои задачи:\n\n"
            for i, row in enumerate(rows, 1):
                text += f"{i}. {row['task']}\n"
            # show list and a back button (so user can return to menu)
            try:
                await query.edit_message_text(text, reply_markup=list_with_back_markup())
                user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
            except Exception:
                await query.message.reply_text(text)

    elif query.data == "delete":
        rows = await get_tasks_db(user_id)
        if not rows:
            try:
                await query.edit_message_text("У тебя пока нет задач 😅", reply_markup=main_menu_markup())
                user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
            except Exception:
                await query.message.reply_text("У тебя пока нет задач 😅")
        else:
            # build keyboard with delete buttons
            kb = delete_keyboard(rows)
            try:
                await query.edit_message_text("Выбери задачу для удаления:", reply_markup=kb)
                user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
            except Exception:
                await query.message.reply_text("Выбери задачу для удаления:", reply_markup=kb)

    elif query.data.startswith("del_"):
        # delete by index (0-based)
        try:
            index = int(query.data.split("_", 1)[1])
        except Exception:
            await query.answer("Ошибка: неверный идентификатор задачи 😅", show_alert=True)
            return
        ok, removed_task = await delete_task_by_index(user_id, index)
        if ok:
            # small popup confirmation (не засоряем чат)
            await query.answer(f"Задача удалена: {removed_task}")
            # re-render delete list or return to menu if empty
            rows = await get_tasks_db(user_id)
            if not rows:
                try:
                    await query.edit_message_text("Все задачи удалены.", reply_markup=main_menu_markup())
                    user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
                except Exception:
                    await query.message.reply_text("Все задачи удалены.")
            else:
                kb = delete_keyboard(rows)
                try:
                    await query.edit_message_text("Выбери задачу для удаления:", reply_markup=kb)
                    user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
                except Exception:
                    await query.message.reply_text("Выбери задачу для удаления:", reply_markup=kb)
        else:
            await query.answer("Ошибка: задача не найдена 😅", show_alert=True)

    elif query.data == "help":
        help_text = (
            "📖 Помощь:\n"
            "1️⃣ /add – добавить задачу\n"
            "2️⃣ /list – показать список задач\n"
            "3️⃣ Удалить задачу – через меню\n\n"
            "Нажми ↩ Назад чтобы вернуться."
        )
        try:
            await query.edit_message_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩ Назад", callback_data="back")]]))
            user_last_menu_msg[user_id] = (query.message.chat_id, query.message.message_id)
        except Exception:
            await query.message.reply_text(help_text)

    elif query.data == "back":
        # return to main menu (edit the message if possible)
        chat_id = query.message.chat_id
        last_saved = user_last_menu_msg.get(user_id)
        msg_id = last_saved[1] if last_saved else query.message.message_id
        new_chat_id, new_msg_id = await show_main_menu_and_store(context.bot, user_id, chat_id, msg_id)
        user_last_menu_msg[user_id] = (new_chat_id, new_msg_id)

    else:
        # fallback for unknown callback
        await query.answer()

async def add_task_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /add command — ask for task via normal message
    user_id = str(update.message.from_user.id)
    user_state[user_id] = "ADDING_TASK"
    # prefer to edit saved menu if exists
    last = user_last_menu_msg.get(user_id)
    if last:
        chat_id, message_id = last
        try:
            await context.bot.edit_message_text("Напиши задачу, и я сохраню её ✅", chat_id=chat_id, message_id=message_id)
            return
        except Exception:
            pass
    await update.message.reply_text("Напиши задачу, и я сохраню её ✅")

async def list_tasks_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /list command from text — just show tasks in one message
    user_id = str(update.message.from_user.id)
    rows = await get_tasks_db(user_id)
    if not rows:
        await update.message.reply_text("У тебя пока нет задач 😅")
        return
    text = "📝 Твои задачи:\n\n"
    for i, row in enumerate(rows, 1):
        text += f"{i}. {row['task']}\n"
    await update.message.reply_text(text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    state = user_state.get(user_id)
    if state == "ADDING_TASK":
        task = update.message.text
        await add_task_db(user_id, task)
        user_state[user_id] = None

        # Try to edit saved menu message to show confirmation + menu (minimize spam)
        last = user_last_menu_msg.get(user_id)
        if last:
            chat_id, message_id = last
            try:
                text = f"Задача добавлена: {task}\n\n{MAIN_MENU_TEXT}"
                await context.bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=main_menu_markup())
                return
            except Exception:
                logging.exception("Не удалось отредактировать сохранённое меню; отправлю отдельное сообщение.")

        # fallback: send confirmation and new menu
        await update.message.reply_text(f"Задача добавлена: {task}")
        msg = await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=main_menu_markup())
        user_last_menu_msg[user_id] = (update.message.chat_id, msg.message_id)

# ---------- main ----------
def main():
    TOKEN = load_token()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_task_cmd))
    app.add_handler(CommandHandler("list", list_tasks_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    loop = asyncio.get_event_loop()

    async def init_all():
        await init_db()
        # set commands
        await app.bot.set_my_commands([
            ("start", "Запуск бота"),
            ("add", "Добавить задачу"),
            ("list", "Показать список задач"),
            ("help", "Помощь по командам"),
        ])

    loop.run_until_complete(init_all())
    app.run_polling()

if __name__ == "__main__":
    main()
