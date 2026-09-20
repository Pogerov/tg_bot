import asyncio
import threading
import sqlite3
import logging
import os

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder

from flask import Flask

# =====================================================================
# КОНФИГ
# =====================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8703713200:AAFvtyjtYIygdC4UdTjP5lpCLNzOrHvrfYw")
ADMIN_ID  = int(os.environ.get("ADMIN_ID", "7753887058"))
DB_PATH   = os.environ.get("DB_PATH", "tournament.db")

if not BOT_TOKEN:
    print("[!] BOT_TOKEN не задан! Установи переменную окружения BOT_TOKEN.")

# =====================================================================
# БАЗА ДАННЫХ
# =====================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            nickname    TEXT NOT NULL,
            photo_id    TEXT NOT NULL,
            status      TEXT DEFAULT 'pending',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('applications_open', '1')")
    conn.commit()
    conn.close()


def db_add(user_id, username, nickname, photo_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO participants (user_id, username, nickname, photo_id, status)
        VALUES (?, ?, ?, ?, 'pending')
    """, (user_id, username, nickname, photo_id))
    conn.commit()
    conn.close()


def db_get(user_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT user_id, username, nickname, photo_id, status FROM participants WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    return row


def db_by_status(status):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT user_id, username, nickname, photo_id, status FROM participants WHERE status = ? ORDER BY created_at",
        (status,)
    ).fetchall()
    conn.close()
    return rows


def db_update_status(user_id, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE participants SET status = ? WHERE user_id = ?", (status, user_id))
    conn.commit()
    conn.close()


def db_delete(user_id):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM participants WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_setting(key):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else None


def set_setting(key, value):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    conn.commit()
    conn.close()


def applications_open() -> bool:
    return get_setting("applications_open") == "1"


# =====================================================================
# КЛАВИАТУРЫ
# =====================================================================
def user_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Ник"), KeyboardButton(text="Фото")]],
        resize_keyboard=True
    )


def cancel_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отмена")]],
        resize_keyboard=True
    )


def admin_kb():
    is_open = applications_open()
    close_text = "🔒 Закрыть заявки" if is_open else "🔓 Открыть заявки"
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📥 Новые заявки")],
            [KeyboardButton(text="👥 Игроки")],
            [KeyboardButton(text=close_text)]
        ],
        resize_keyboard=True
    )


def pending_inline(participants):
    b = InlineKeyboardBuilder()
    for p in participants:
        b.button(text=f"📝 {p[2]}", callback_data=f"view:{p[0]}")
    b.adjust(1)
    return b.as_markup()


def accept_inline(user_id):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Принять человека!", callback_data=f"accept:{user_id}")
    b.button(text="❌ Отклонить", callback_data=f"reject:{user_id}")
    b.adjust(1)
    return b.as_markup()


def players_inline(participants):
    b = InlineKeyboardBuilder()
    for p in participants:
        status = p[4]
        icon = "🏆" if status == "won" else "👤"
        b.button(text=f"{icon} {p[2]}", callback_data=f"view_player:{p[0]}")
    b.adjust(1)
    return b.as_markup()


def player_actions_inline(user_id):
    b = InlineKeyboardBuilder()
    b.button(text="🏆 Выиграл", callback_data=f"win:{user_id}")
    b.button(text="🚪 Выбыл", callback_data=f"lose:{user_id}")
    b.adjust(2)
    return b.as_markup()


def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


# =====================================================================
# РОУТЕР
# =====================================================================
router = Router()


class UserFlow(StatesGroup):
    waiting_nick = State()
    waiting_photo = State()


@router.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()

    if is_admin(message.from_user.id):
        await message.answer(
            "👑 <b>Админ-панель турнира</b>",
            parse_mode="HTML",
            reply_markup=admin_kb()
        )
        return

    if not applications_open():
        await message.answer(
            "😔 Извините, подача заявок <b>закрыта</b>, турнир набрал нужное кол-во людей!\n"
            "Удачного просмотра за игрой! 🎮",
            parse_mode="HTML"
        )
        return

    await message.answer(
        "👋 Добро пожаловать на <b>турнир</b>!\n\n"
        "Нажмите <b>Ник</b>, чтобы начать регистрацию.",
        parse_mode="HTML",
        reply_markup=user_kb()
    )


@router.message(F.text == "Ник")
async def nick_button(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        return
    if not applications_open():
        await message.answer("😔 Извините, подача заявок <b>закрыта</b>!", parse_mode="HTML")
        return
    await state.set_state(UserFlow.waiting_nick)
    await message.answer(
        "✏️ Введите ваш <b>ник</b> (любое слово):",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@router.message(F.text == "Отмена")
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id):
        await message.answer("❌ Отменено.", reply_markup=admin_kb())
    else:
        await message.answer("❌ Отменено.", reply_markup=user_kb())


@router.message(UserFlow.waiting_nick)
async def nick_input(message: Message, state: FSMContext):
    if not applications_open():
        await state.clear()
        await message.answer("😔 Извините, подача заявок <b>закрыта</b>!", parse_mode="HTML")
        return

    nick = (message.text or "").strip()
    if len(nick) < 2 or len(nick) > 32:
        await message.answer("⚠️ Ник должен быть от 2 до 32 символов. Попробуйте ещё.")
        return
    await state.update_data(nickname=nick)
    await state.set_state(UserFlow.waiting_photo)
    await message.answer(
        f"✅ Отлично, <b>{nick}</b>!\n"
        f"Теперь отправьте своё <b>фото</b> или аватарку для завершения регистрации на турнир!",
        parse_mode="HTML",
        reply_markup=cancel_kb()
    )


@router.message(F.text == "Фото")
async def photo_button(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        return
    if not applications_open():
        await message.answer("😔 Извините, подача заявок <b>закрыта</b>!", parse_mode="HTML")
        return
    data = await state.get_data()
    if not data.get("nickname"):
        await message.answer("⚠️ Сначала введите ник — нажмите <b>Ник</b>.", parse_mode="HTML")
        return
    await state.set_state(UserFlow.waiting_photo)
    await message.answer("📸 Отправьте фото.", reply_markup=cancel_kb())


@router.message(UserFlow.waiting_photo, F.photo)
async def photo_input(message: Message, state: FSMContext):
    if not applications_open():
        await state.clear()
        await message.answer("😔 Извините, подача заявок <b>закрыта</b>!", parse_mode="HTML", reply_markup=user_kb())
        return

    data = await state.get_data()
    nick = data.get("nickname")
    if not nick:
        await message.answer("⚠️ Сначала введите ник.")
        return

    photo_id = message.photo[-1].file_id
    user = message.from_user

    db_add(user.id, user.username or "", nick, photo_id)
    await state.clear()

    await message.answer("✅ Отлично! Подождите...", reply_markup=user_kb())

    try:
        await message.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_id,
            caption=(
                f"🎉 <b>Человек зарегистрировался!</b>\n\n"
                f"👤 Ник: <b>{nick}</b>\n"
                f"🆔 ID: <code>{user.id}</code>"
            ),
            parse_mode="HTML",
            reply_markup=accept_inline(user.id)
        )
    except Exception as e:
        print(f"[!] Не удалось уведомить админа: {e}")


@router.message(UserFlow.waiting_photo)
async def photo_wrong(message: Message, state: FSMContext):
    await message.answer("⚠️ Отправьте именно <b>фото</b>.", parse_mode="HTML")


@router.message(F.text == "📥 Новые заявки")
async def show_pending(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = db_by_status("pending")
    if not rows:
        await message.answer("📭 Нет новых заявок.")
        return
    await message.answer(
        f"📥 <b>Новые заявки: {len(rows)}</b>\n\nНажмите на ник.",
        parse_mode="HTML",
        reply_markup=pending_inline(rows)
    )


@router.message(F.text == "👥 Игроки")
async def show_players(message: Message):
    if not is_admin(message.from_user.id):
        return
    rows = db_by_status("accepted") + db_by_status("won")
    if not rows:
        await message.answer("📭 Нет принятых игроков.")
        return
    await message.answer(
        f"👥 <b>Игроки: {len(rows)}</b>\n\nНажмите на ник.",
        parse_mode="HTML",
        reply_markup=players_inline(rows)
    )


@router.message(F.text.in_(["🔒 Закрыть заявки", "🔓 Открыть заявки"]))
async def toggle_applications(message: Message):
    if not is_admin(message.from_user.id):
        return

    if "Закрыть" in message.text:
        set_setting("applications_open", "0")
        await message.answer(
            "🔒 <b>Заявки закрыты!</b>\n\nНовые регистрации заблокированы.",
            parse_mode="HTML",
            reply_markup=admin_kb()
        )
    else:
        set_setting("applications_open", "1")
        await message.answer(
            "🔓 <b>Заявки открыты!</b>\n\nНовые регистрации разрешены.",
            parse_mode="HTML",
            reply_markup=admin_kb()
        )


@router.callback_query(F.data.startswith("view:"))
async def view_pending(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    user_id = int(call.data.split(":")[1])
    p = db_get(user_id)
    if not p:
        await call.answer("Не найден.", show_alert=True)
        return

    _, username, nickname, photo_id, status = p

    await call.message.answer_photo(
        photo=photo_id,
        caption=(
            f"👤 Ник: <b>{nickname}</b>\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"📛 Username: @{username if username else '—'}\n"
            f"📌 Статус: <b>{status}</b>"
        ),
        parse_mode="HTML",
        reply_markup=accept_inline(user_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("accept:"))
async def accept_user(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    user_id = int(call.data.split(":")[1])
    p = db_get(user_id)
    if not p:
        await call.answer("Не найден.", show_alert=True)
        return

    db_update_status(user_id, "accepted")
    nickname = p[2]

    try:
        await call.message.edit_caption(
            caption=f"✅ <b>{nickname}</b> принят на турнир!",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await call.answer("Принят!")


@router.callback_query(F.data.startswith("reject:"))
async def reject_user(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    user_id = int(call.data.split(":")[1])
    db_delete(user_id)

    try:
        await call.message.edit_caption(
            caption="❌ Заявка отклонена и удалена.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await call.answer("Удалено.")


@router.callback_query(F.data.startswith("view_player:"))
async def view_player(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    user_id = int(call.data.split(":")[1])
    p = db_get(user_id)
    if not p:
        await call.answer("Не найден.", show_alert=True)
        return

    _, username, nickname, photo_id, status = p
    status_text = "🏆 Выиграл" if status == "won" else "👤 В игре"

    await call.message.answer_photo(
        photo=photo_id,
        caption=(
            f"👤 Ник: <b>{nickname}</b>\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"📛 Username: @{username if username else '—'}\n"
            f"📌 Статус: <b>{status_text}</b>"
        ),
        parse_mode="HTML",
        reply_markup=player_actions_inline(user_id)
    )
    await call.answer()


@router.callback_query(F.data.startswith("win:"))
async def win_user(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    user_id = int(call.data.split(":")[1])
    p = db_get(user_id)
    if not p:
        await call.answer("Не найден.", show_alert=True)
        return

    db_update_status(user_id, "won")
    nickname = p[2]

    try:
        await call.message.edit_caption(
            caption=f"🏆 <b>{nickname}</b> — ВЫИГРАЛ! Остаётся в турнире.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await call.answer("Статус: Выиграл")


@router.callback_query(F.data.startswith("lose:"))
async def lose_user(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("Нет доступа.", show_alert=True)
        return

    user_id = int(call.data.split(":")[1])
    p = db_get(user_id)
    if not p:
        await call.answer("Не найден.", show_alert=True)
        return

    nickname = p[2]
    db_delete(user_id)

    try:
        await call.message.edit_caption(
            caption=f"🚪 <b>{nickname}</b> — ВЫБЫЛ и удалён из турнира.",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await call.answer("Удалён.")


# =====================================================================
# FLASK (для UptimeRobot)
# =====================================================================
flask_app = Flask(__name__)


@flask_app.route("/")
@flask_app.route("/health")
def health():
    return "OK", 200


def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


# =====================================================================
# ЗАПУСК
# =====================================================================
async def main():
    init_db()
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.include_router(router)

    print("[BOT] Started.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    threading.Thread(target=run_flask, daemon=True).start()
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("[BOT] Stopped.")
