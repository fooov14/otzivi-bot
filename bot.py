"""
Telegram-бот для продажи клиентов (заявок) из ваших Telegram-пабликов.

Всё содержимое (тексты, кнопки, цена, ссылка на оплату) редактируется
прямо в Telegram командой /admin — код трогать не нужно.
"""

import asyncio
import logging
import os
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# Конфигурация
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "config.env"))

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}
DB_PATH = os.path.join(BASE_DIR, "bot.db")

# Все настраиваемые параметры: ключ -> (название в админке, значение по умолчанию, тип)
# Тип: text — любой текст, int — целое число, url — ссылка
SETTINGS = {
    "btn_info": ("Кнопка «Информация»", "ℹ️ Информация", "text"),
    "btn_prices": ("Кнопка «Цены»", "💰 Цены и оплата", "text"),
    "btn_buy": ("Кнопка «Купить»", "🛒 Купить клиентов", "text"),
    "btn_contact": ("Кнопка «Связаться»", "📞 Связаться", "text"),
    "welcome": (
        "Приветствие (/start)",
        "Привет! 👋\n\n"
        "Мы приводим клиентов под вашу нишу из наших тематических Telegram-пабликов.\n\n"
        "Выберите раздел в меню ниже.",
        "text",
    ),
    "info": (
        "Текст «Информация»",
        "Как это работает:\n\n"
        "1. Вы оставляете заявку: ниша, город и нужное количество клиентов.\n"
        "2. Мы размещаем ваше предложение в наших пабликах с подходящей аудиторией.\n"
        "3. Заинтересованные люди оставляют контакты — мы передаём их вам.\n\n"
        "Оплата — за каждого переданного клиента.",
        "text",
    ),
    "prices": (
        "Текст «Цены»",
        "Стоимость: {price} ₽ за одного клиента.\n"
        "Минимальный заказ: {min_qty} шт.\n\n"
        "Способы оплаты: банковская карта, СБП.",
        "text",
    ),
    "contact": ("Текст «Связаться»", "По всем вопросам пишите: @your_username", "text"),
    "price": ("Цена за 1 клиента, ₽", "500", "int"),
    "min_qty": ("Минимальный заказ, шт.", "1", "int"),
    "pay_url": ("Ссылка на оплату", "https://example.com/pay", "url"),
    "ask_niche": ("Вопрос: ниша", "Напишите вашу нишу (например: барбершоп, ремонт квартир):", "text"),
    "ask_city": ("Вопрос: город", "В каком городе нужны клиенты?", "text"),
    "ask_qty": ("Вопрос: количество", "Сколько клиентов нужно? Отправьте число (минимум {min_qty}).", "text"),
    "pay_text": (
        "Текст перед оплатой",
        "Ваша заявка №{order_id}\n\n"
        "Ниша: {niche}\nГород: {city}\nКоличество: {qty} шт.\n"
        "Сумма к оплате: {total} ₽\n\n"
        "Нажмите «Оплатить», а после оплаты — «Я оплатил».",
        "text",
    ),
    "paid_text": (
        "Текст после «Я оплатил»",
        "Спасибо! Заявка №{order_id} отправлена на проверку. Мы свяжемся с вами в ближайшее время.",
        "text",
    ),
    "confirmed_text": (
        "Текст: оплата подтверждена",
        "✅ Оплата по заявке №{order_id} подтверждена. Начинаем работу!",
        "text",
    ),
    "rejected_text": (
        "Текст: оплата не найдена",
        "❌ Мы не нашли оплату по заявке №{order_id}. Напишите нам, разберёмся.",
        "text",
    ),
}

STATUS_NAMES = {
    "new": "🆕 создана, не оплачена",
    "check": "⏳ ждёт проверки оплаты",
    "paid": "✅ оплачена",
    "rejected": "❌ отклонена",
}

# ----------------------------------------------------------------------------
# База данных (SQLite, файл bot.db рядом с ботом)
# ----------------------------------------------------------------------------


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, "
            "name TEXT, joined TEXT)"
        )
        c.execute(
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER, username TEXT, niche TEXT, city TEXT, qty INTEGER, "
            "total INTEGER, status TEXT, created TEXT)"
        )
        for key, (_, default, _) in SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, default))


def get(key: str) -> str:
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else SETTINGS[key][1]


def set_value(key: str, value: str):
    with db() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def get_int(key: str) -> int:
    try:
        return int(get(key))
    except ValueError:
        return int(SETTINGS[key][1])


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def fmt(key: str, **extra) -> str:
    """Текст из настроек с подстановкой {price}, {min_qty} и т.п."""
    values = _Safe(price=get_int("price"), min_qty=get_int("min_qty"), **extra)
    text = get(key)
    try:
        return text.format_map(values)
    except (ValueError, IndexError):
        return text  # если в тексте сломаны фигурные скобки — показываем как есть


def save_user(m: Message):
    u = m.from_user
    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO users (id, username, name, joined) VALUES (?, ?, ?, ?)",
            (u.id, u.username or "", u.full_name, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )


def create_order(user, niche, city, qty, total) -> int:
    with db() as c:
        cur = c.execute(
            "INSERT INTO orders (user_id, username, niche, city, qty, total, status, created) "
            "VALUES (?, ?, ?, ?, ?, ?, 'new', ?)",
            (user.id, user.username or "", niche, city, qty, total,
             datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        return cur.lastrowid


def get_order(order_id: int):
    with db() as c:
        return c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def set_order_status(order_id: int, status: str):
    with db() as c:
        c.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


# ----------------------------------------------------------------------------
# Клавиатуры
# ----------------------------------------------------------------------------


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu():
    return kb([
        [InlineKeyboardButton(text=get("btn_info"), callback_data="info")],
        [InlineKeyboardButton(text=get("btn_prices"), callback_data="prices")],
        [InlineKeyboardButton(text=get("btn_buy"), callback_data="buy")],
        [InlineKeyboardButton(text=get("btn_contact"), callback_data="contact")],
    ])


MENU_KEYS = ("btn_info", "btn_prices", "btn_buy", "btn_contact")


def reply_menu():
    """Постоянные кнопки меню внизу экрана (всегда видны)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get("btn_buy"))],
            [KeyboardButton(text=get("btn_info")), KeyboardButton(text=get("btn_prices"))],
            [KeyboardButton(text=get("btn_contact"))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def menu_key(text):
    for key in MENU_KEYS:
        if text == get(key):
            return key
    return None


def back_menu():
    return kb([[InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]])


def admin_menu():
    return kb([
        [InlineKeyboardButton(text="✏️ Тексты, кнопки, цена, оплата", callback_data="adm:settings")],
        [InlineKeyboardButton(text="📋 Заявки", callback_data="adm:orders")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
    ])


def settings_menu():
    rows = [[InlineKeyboardButton(text=label, callback_data=f"edit:{key}")]
            for key, (label, _, _) in SETTINGS.items()]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")])
    return kb(rows)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ----------------------------------------------------------------------------
# Состояния диалогов
# ----------------------------------------------------------------------------


class Buy(StatesGroup):
    niche = State()
    city = State()
    qty = State()


class Edit(StatesGroup):
    value = State()


router = Router()

# ----------------------------------------------------------------------------
# Пользовательская часть
# ----------------------------------------------------------------------------


@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    save_user(m)
    await m.answer(fmt("welcome"), reply_markup=reply_menu())


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отменено.", reply_markup=reply_menu())


async def ask_niche(m: Message, state: FSMContext):
    await state.set_state(Buy.niche)
    await m.answer(fmt("ask_niche") + "\n\n(для отмены — /cancel)")


@router.message(lambda m: m.text is not None and menu_key(m.text) is not None)
async def reply_menu_press(m: Message, state: FSMContext):
    """Нажатие на постоянную кнопку меню внизу — работает в любой момент."""
    await state.clear()
    save_user(m)
    key = menu_key(m.text)
    if key == "btn_buy":
        await ask_niche(m, state)
    else:
        page = {"btn_info": "info", "btn_prices": "prices", "btn_contact": "contact"}[key]
        await m.answer(fmt(page), reply_markup=reply_menu())


@router.callback_query(F.data == "menu")
async def cb_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer(fmt("welcome"), reply_markup=main_menu())
    await c.answer()


@router.callback_query(F.data.in_({"info", "prices", "contact"}))
async def cb_page(c: CallbackQuery):
    await c.message.answer(fmt(c.data), reply_markup=back_menu())
    await c.answer()


@router.callback_query(F.data == "buy")
async def cb_buy(c: CallbackQuery, state: FSMContext):
    await ask_niche(c.message, state)
    await c.answer()


@router.message(Buy.niche, F.text)
async def buy_niche(m: Message, state: FSMContext):
    await state.update_data(niche=m.text.strip()[:200])
    await state.set_state(Buy.city)
    await m.answer(fmt("ask_city"))


@router.message(Buy.city, F.text)
async def buy_city(m: Message, state: FSMContext):
    await state.update_data(city=m.text.strip()[:100])
    await state.set_state(Buy.qty)
    await m.answer(fmt("ask_qty"))


@router.message(Buy.qty, F.text)
async def buy_qty(m: Message, state: FSMContext):
    text = m.text.strip()
    min_qty = get_int("min_qty")
    if not text.isdigit() or int(text) < min_qty or int(text) > 100000:
        await m.answer(f"Отправьте число не меньше {min_qty}.")
        return
    qty = int(text)
    data = await state.get_data()
    await state.clear()
    total = qty * get_int("price")
    order_id = create_order(m.from_user, data["niche"], data["city"], qty, total)

    pay_url = get("pay_url")
    rows = []
    if pay_url.startswith(("http://", "https://")):
        rows.append([InlineKeyboardButton(text=f"💳 Оплатить {total} ₽", url=pay_url)])
    rows.append([InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{order_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")])
    await m.answer(
        fmt("pay_text", order_id=order_id, niche=data["niche"], city=data["city"],
            qty=qty, total=total),
        reply_markup=kb(rows),
    )


@router.callback_query(F.data.startswith("paid:"))
async def cb_paid(c: CallbackQuery, bot: Bot):
    order_id = int(c.data.split(":")[1])
    order = get_order(order_id)
    if not order or order["user_id"] != c.from_user.id:
        await c.answer("Заявка не найдена", show_alert=True)
        return
    if order["status"] != "new":
        await c.answer("Эта заявка уже отправлена на проверку", show_alert=True)
        return
    set_order_status(order_id, "check")
    await c.message.answer(fmt("paid_text", order_id=order_id), reply_markup=back_menu())
    await c.answer()

    who = f"@{order['username']}" if order["username"] else f"id {order['user_id']}"
    text = (
        f"💸 Новая оплата на проверку — заявка №{order_id}\n\n"
        f"Клиент: {who}\nНиша: {order['niche']}\nГород: {order['city']}\n"
        f"Количество: {order['qty']} шт.\nСумма: {order['total']} ₽"
    )
    buttons = kb([[
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"ok:{order_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no:{order_id}"),
    ]])
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, reply_markup=buttons)
        except Exception as e:  # админ не запускал бота и т.п.
            logging.warning("Не удалось уведомить админа %s: %s", admin_id, e)


@router.message(StateFilter(Buy))
async def buy_wrong(m: Message):
    await m.answer("Пожалуйста, отправьте ответ текстом. Для отмены — /cancel")


# ----------------------------------------------------------------------------
# Админка (/admin)
# ----------------------------------------------------------------------------


@router.message(Command("admin"))
async def admin(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.clear()
    await m.answer("⚙️ Админ-панель", reply_markup=admin_menu())


@router.callback_query(F.data.startswith("adm:"))
async def cb_admin(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    await state.clear()
    action = c.data.split(":")[1]

    if action == "home":
        await c.message.answer("⚙️ Админ-панель", reply_markup=admin_menu())

    elif action == "settings":
        await c.message.answer("Что изменить?", reply_markup=settings_menu())

    elif action == "orders":
        with db() as conn:
            orders = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 15").fetchall()
        if not orders:
            await c.message.answer("Заявок пока нет.", reply_markup=admin_menu())
        else:
            lines = []
            for o in orders:
                who = f"@{o['username']}" if o["username"] else f"id {o['user_id']}"
                lines.append(
                    f"№{o['id']} · {o['created']} · {who}\n"
                    f"{o['niche']}, {o['city']}, {o['qty']} шт., {o['total']} ₽\n"
                    f"{STATUS_NAMES.get(o['status'], o['status'])}"
                )
            await c.message.answer("Последние заявки:\n\n" + "\n\n".join(lines),
                                   reply_markup=admin_menu())

    elif action == "stats":
        with db() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            paid = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(total), 0) FROM orders WHERE status = 'paid'"
            ).fetchone()
        await c.message.answer(
            f"📊 Статистика\n\nПользователей: {users}\nЗаявок всего: {orders}\n"
            f"Оплачено заявок: {paid[0]}\nВыручка: {paid[1]} ₽",
            reply_markup=admin_menu(),
        )
    await c.answer()


@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    key = c.data.split(":", 1)[1]
    if key not in SETTINGS:
        await c.answer()
        return
    label, _, kind = SETTINGS[key]
    hint = {
        "int": "Отправьте число.",
        "url": "Отправьте ссылку, начинающуюся с https://",
        "text": "Отправьте новый текст.",
    }[kind]
    if kind == "text" and key not in ("btn_info", "btn_prices", "btn_buy", "btn_contact"):
        hint += ("\n\nМожно вставлять: {price} — цена, {min_qty} — минимальный заказ."
                 "\nВ текстах заявки также: {order_id}, {niche}, {city}, {qty}, {total}.")
    await state.set_state(Edit.value)
    await state.update_data(key=key)
    await c.message.answer(f"«{label}»\n\nСейчас:")
    await c.message.answer(get(key))
    await c.message.answer(f"{hint}\nДля отмены — /cancel")
    await c.answer()


@router.message(Edit.value, F.text)
async def edit_value(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        await state.clear()
        return
    key = (await state.get_data())["key"]
    label, _, kind = SETTINGS[key]
    value = m.text.strip()

    if kind == "int" and (not value.isdigit() or int(value) < 1):
        await m.answer("Нужно целое число больше нуля. Попробуйте ещё раз или /cancel")
        return
    if kind == "url" and not value.startswith(("http://", "https://")):
        await m.answer("Ссылка должна начинаться с https://. Попробуйте ещё раз или /cancel")
        return
    if key.startswith("btn_") and len(value) > 60:
        await m.answer("Слишком длинная надпись для кнопки (максимум 60 символов).")
        return

    set_value(key, value)
    await state.clear()
    if key in MENU_KEYS:
        await m.answer("Кнопки меню обновлены 👇", reply_markup=reply_menu())
    await m.answer(f"✅ Сохранено: «{label}»", reply_markup=settings_menu())


@router.callback_query(F.data.startswith(("ok:", "no:")))
async def cb_confirm(c: CallbackQuery, bot: Bot):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    action, order_id = c.data.split(":")
    order_id = int(order_id)
    order = get_order(order_id)
    if not order:
        await c.answer("Заявка не найдена", show_alert=True)
        return
    status = "paid" if action == "ok" else "rejected"
    set_order_status(order_id, status)
    text_key = "confirmed_text" if status == "paid" else "rejected_text"
    try:
        await bot.send_message(order["user_id"], fmt(text_key, order_id=order_id))
    except Exception as e:
        logging.warning("Не удалось написать клиенту: %s", e)
    await c.message.edit_text(c.message.text + f"\n\nСтатус: {STATUS_NAMES[status]}")
    await c.answer("Готово")


# ----------------------------------------------------------------------------
# Запуск
# ----------------------------------------------------------------------------


async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or BOT_TOKEN.startswith("ВСТАВЬТЕ"):
        raise SystemExit("Укажите BOT_TOKEN в файле config.env")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS не указан в config.env — админка никому не доступна")
    init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
