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
from aiogram.fsm.storage.base import BaseStorage, StorageKey
import json
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
# База должна лежать в постоянном хранилище, иначе хостинг стирает её при обновлении.
# На Bothost это /app/data. Можно переопределить переменной окружения DB_PATH.
DB_PATH = os.getenv("DB_PATH") or (
    "/app/data/bot.db" if os.path.isdir("/app/data") else os.path.join(BASE_DIR, "bot.db")
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Все настраиваемые параметры: ключ -> (название в админке, значение по умолчанию, тип)
# Тип: text — любой текст, int — целое число, url — ссылка
SETTINGS = {
    "btn_info": ("Кнопка «Информация»", "ℹ️ Информация", "text"),
    "btn_prices": ("Кнопка «Цены»", "💰 Цены и оплата", "text"),
    "btn_buy": ("Кнопка «Купить»", "🛒 Купить клиентов", "text"),
    "btn_contact": ("Кнопка «Связаться»", "📞 Связаться", "text"),
    "btn_orders": ("Кнопка «Мои заказы»", "📦 Мои заказы", "text"),
    "btn_reviews": ("Кнопка «Отзывы»", "⭐ Отзывы", "text"),
    "reviews_empty": ("Текст: отзывов нет", "Отзывов пока нет. Станьте первым!", "text"),
    "review_ask_stars": ("Вопрос: оценка", "Оцените нашу работу от 1 до 5:", "text"),
    "review_ask_text": ("Вопрос: комментарий", "Напишите пару слов о работе с нами:", "text"),
    "review_thanks": ("Текст: спасибо за отзыв", "Спасибо за отзыв! 🙏", "text"),
    "no_orders": ("Текст: заказов нет", "У вас пока нет заказов. Нажмите «🛒 Купить клиентов», чтобы оформить первый.", "text"),
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
    "done_text": (
        "Текст: заказ выполнен",
        "🏁 Заказ №{order_id} выполнен! Спасибо, что выбрали нас.",
        "text",
    ),
    "rejected_text": (
        "Текст: оплата не найдена",
        "❌ Мы не нашли оплату по заявке №{order_id}. Напишите нам, разберёмся.",
        "text",
    ),
}

# Статусы для админа
STATUS_NAMES = {
    "new": "🆕 создана, не оплачена",
    "check": "⏳ ждёт проверки оплаты",
    "paid": "🔄 оплачена, в процессе",
    "done": "🏁 выполнена",
    "rejected": "❌ отклонена",
}

# Статусы, которые видит клиент в «Мои заказы»
CLIENT_STATUS = {
    "new": "🕓 Ожидает оплаты",
    "check": "⏳ Оплата на проверке",
    "paid": "🔄 Оплата принята — в процессе",
    "done": "🏁 Выполнен",
    "rejected": "❌ Оплата не найдена",
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
        c.execute(
            "CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER UNIQUE, username TEXT, name TEXT, stars INTEGER, text TEXT, created TEXT)"
        )
        # Миграция: если bot.db остался от старой версии бота, в таблице orders
        # может не хватать колонок — тогда create_order падает молча.
        have = {row["name"] for row in c.execute("PRAGMA table_info(orders)")}
        for col, typ in (("user_id", "INTEGER"), ("username", "TEXT"), ("niche", "TEXT"),
                         ("city", "TEXT"), ("qty", "INTEGER"), ("total", "INTEGER"),
                         ("status", "TEXT"), ("created", "TEXT")):
            if col not in have:
                c.execute(f"ALTER TABLE orders ADD COLUMN {col} {typ}")
                logging.warning("В таблицу orders добавлена колонка %s", col)
        for key, (_, default, _) in SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, default))


class SQLiteStorage(BaseStorage):
    """Хранит шаг диалога в bot.db, чтобы он не терялся при перезапуске бота."""

    def __init__(self):
        with db() as c:
            c.execute("CREATE TABLE IF NOT EXISTS fsm (k TEXT PRIMARY KEY, state TEXT, data TEXT)")

    @staticmethod
    def _k(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.destiny}"

    def _row(self, key):
        with db() as c:
            return c.execute("SELECT state, data FROM fsm WHERE k = ?", (self._k(key),)).fetchone()

    async def set_state(self, key: StorageKey, state=None) -> None:
        value = state.state if isinstance(state, State) else state
        with db() as c:
            c.execute("INSERT INTO fsm (k, state, data) VALUES (?, ?, '{}') "
                      "ON CONFLICT(k) DO UPDATE SET state = excluded.state", (self._k(key), value))

    async def get_state(self, key: StorageKey):
        row = self._row(key)
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data) -> None:
        payload = json.dumps(dict(data), ensure_ascii=False)
        with db() as c:
            c.execute("INSERT INTO fsm (k, state, data) VALUES (?, NULL, ?) "
                      "ON CONFLICT(k) DO UPDATE SET data = excluded.data", (self._k(key), payload))

    async def get_data(self, key: StorageKey) -> dict:
        row = self._row(key)
        return json.loads(row["data"] or "{}") if row else {}

    async def close(self) -> None:
        pass


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
        result = text.format_map(values)
    except Exception:
        result = text  # если в тексте сломаны фигурные скобки — показываем как есть
    if not result.strip():  # пустой текст Telegram не отправит
        result = SETTINGS[key][1].format_map(values) if key in SETTINGS else "—"
    return result


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
        [InlineKeyboardButton(text=get("btn_orders"), callback_data="orders")],
        [InlineKeyboardButton(text=get("btn_reviews"), callback_data="rev:0")],
        [InlineKeyboardButton(text=get("btn_contact"), callback_data="contact")],
    ])


MENU_KEYS = ("btn_info", "btn_prices", "btn_buy", "btn_orders", "btn_reviews", "btn_contact")


def reply_menu():
    """Постоянные кнопки меню внизу экрана (всегда видны)."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=get("btn_buy")), KeyboardButton(text=get("btn_orders"))],
            [KeyboardButton(text=get("btn_info")), KeyboardButton(text=get("btn_prices"))],
            [KeyboardButton(text=get("btn_reviews")), KeyboardButton(text=get("btn_contact"))],
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
        [InlineKeyboardButton(text="⭐ Отзывы", callback_data="adm:reviews")],
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


class Review(StatesGroup):
    text = State()


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


def my_orders_text(user_id: int) -> str:
    with db() as conn:
        orders = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,)
        ).fetchall()
    if not orders:
        return fmt("no_orders")
    lines = []
    for o in orders:
        lines.append(
            f"Заказ №{o['id']} от {o['created']}\n"
            f"{o['niche']}, {o['city']} — {o['qty']} шт.\n"
            f"Сумма: {o['total']} ₽\n"
            f"Статус: {CLIENT_STATUS.get(o['status'], o['status'])}"
        )
    return "📦 Ваши заказы:\n\n" + "\n\n".join(lines)


@router.message(lambda m: m.text is not None and menu_key(m.text) is not None)
async def reply_menu_press(m: Message, state: FSMContext):
    """Нажатие на постоянную кнопку меню внизу — работает в любой момент."""
    await state.clear()
    save_user(m)
    key = menu_key(m.text)
    if key == "btn_buy":
        await ask_niche(m, state)
    elif key == "btn_orders":
        await m.answer(my_orders_text(m.from_user.id), reply_markup=reply_menu())
    elif key == "btn_reviews":
        text, markup = reviews_page(0)
        await m.answer(text, reply_markup=markup)
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


# ---------------- Отзывы ----------------

REVIEWS_PAGE = 5


def stars_str(n: int) -> str:
    return "⭐" * n + "☆" * (5 - n)


def can_review(user_id: int) -> bool:
    """True, если у пользователя есть выполненный заказ (для отметки «✅ клиент»)."""
    with db() as conn:
        return conn.execute(
            "SELECT 1 FROM orders WHERE user_id = ? AND status = 'done' LIMIT 1", (user_id,)
        ).fetchone() is not None


def reviews_page(page: int):
    with db() as conn:
        total, avg = conn.execute("SELECT COUNT(*), AVG(stars) FROM reviews").fetchone()
        rows_db = conn.execute(
            "SELECT * FROM reviews ORDER BY id DESC LIMIT ? OFFSET ?",
            (REVIEWS_PAGE, page * REVIEWS_PAGE),
        ).fetchall()
    rows = [[InlineKeyboardButton(text="✍️ Написать отзыв", callback_data="rev_write")]]
    if not total:
        return fmt("reviews_empty"), kb(rows + [[InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")]])
    parts = [f"⭐ Рейтинг: {avg:.1f} из 5 · отзывов: {total}"]
    for r in rows_db:
        who = f"@{r['username']}" if r["username"] else r["name"]
        badge = " · ✅ клиент" if can_review(r["user_id"]) else ""
        block = f"{stars_str(r['stars'])}  {who}{badge} · {r['created'][:10]}"
        if r["text"]:
            block += f"\n{r['text']}"
        parts.append(block)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"rev:{page - 1}"))
    if (page + 1) * REVIEWS_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"rev:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")])
    return "\n\n".join(parts), kb(rows)


@router.callback_query(F.data.startswith("rev:"))
async def cb_reviews(c: CallbackQuery):
    text, markup = reviews_page(int(c.data.split(":")[1]))
    if c.data == "rev:0" and not c.message.text.startswith("⭐ Рейтинг"):
        await c.message.answer(text, reply_markup=markup)
    else:
        await c.message.edit_text(text, reply_markup=markup)
    await c.answer()


@router.callback_query(F.data == "rev_write")
async def cb_rev_write(c: CallbackQuery):
    stars = [[InlineKeyboardButton(text=stars_str(n), callback_data=f"rate:{n}")] for n in range(5, 0, -1)]
    await c.message.answer(fmt("review_ask_stars"), reply_markup=kb(stars))
    await c.answer()


@router.callback_query(F.data.startswith("rate:"))
async def cb_rate(c: CallbackQuery, state: FSMContext):
    n = int(c.data.split(":")[1])
    await state.set_state(Review.text)
    await state.update_data(stars=n)
    await c.message.edit_text(f"Ваша оценка: {stars_str(n)}")
    await c.message.answer(
        fmt("review_ask_text"),
        reply_markup=kb([[InlineKeyboardButton(text="Без комментария", callback_data="rev_skip")]]),
    )
    await c.answer()


async def save_review(user, stars: int, text: str, bot: Bot):
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reviews (user_id, username, name, stars, text, created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.username or "", user.full_name, stars, text,
             datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
    who = f"@{user.username}" if user.username else user.full_name
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"⭐ Новый отзыв от {who}\n{stars_str(stars)}\n{text}")
        except Exception:
            pass


@router.message(Review.text, F.text)
async def review_text(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    await save_review(m.from_user, data["stars"], m.text.strip()[:1000], bot)
    await m.answer(fmt("review_thanks"), reply_markup=reply_menu())


@router.callback_query(F.data == "rev_skip")
async def cb_rev_skip(c: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if "stars" not in data:
        await c.answer()
        return
    await state.clear()
    await save_review(c.from_user, data["stars"], "", bot)
    await c.message.edit_text(fmt("review_thanks"))
    await c.answer()


@router.callback_query(F.data == "orders")
async def cb_orders(c: CallbackQuery):
    await c.message.answer(my_orders_text(c.from_user.id), reply_markup=back_menu())
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
    try:
        await send_payment(m, data, qty)
    except Exception as e:
        # Раньше любая ошибка здесь проглатывалась: клиент ничего не получал
        # и видел только меню. Теперь пишем в лог, клиенту и админу.
        logging.exception("Ошибка при оформлении заявки")
        await m.answer("⚠️ Не удалось оформить заявку, попробуйте ещё раз или напишите нам.",
                       reply_markup=reply_menu())
        for admin_id in ADMIN_IDS:
            try:
                await m.bot.send_message(admin_id, f"⚠️ Ошибка при оформлении заявки:\n{type(e).__name__}: {e}")
            except Exception:
                pass


async def send_payment(m: Message, data: dict, qty: int):
    niche = data.get("niche", "—")
    city = data.get("city", "—")
    total = qty * get_int("price")
    order_id = create_order(m.from_user, niche, city, qty, total)
    data = {"niche": niche, "city": city}

    pay_url = get("pay_url")
    rows = []
    if pay_url.startswith(("http://", "https://")):
        rows.append([InlineKeyboardButton(text=f"💳 Оплатить {total} ₽", url=pay_url)])
    rows.append([InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{order_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu")])
    text = fmt("pay_text", order_id=order_id, niche=data["niche"], city=data["city"],
               qty=qty, total=total)
    try:
        await m.answer(text, reply_markup=kb(rows))
    except Exception as e:
        # Чаще всего — Telegram не принял ссылку на оплату. Отправляем без неё и сообщаем админу.
        logging.error("Не удалось отправить сообщение об оплате: %s", e)
        await m.answer(text + f"\n\nСсылка на оплату: {pay_url}", reply_markup=kb(rows[-2:]))
        for admin_id in ADMIN_IDS:
            try:
                await m.bot.send_message(
                    admin_id,
                    f"⚠️ Telegram не принял ссылку на оплату:\n{pay_url}\n\nОшибка: {e}\n\n"
                    "Проверьте ссылку в /admin → «Ссылка на оплату».")
            except Exception:
                pass


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


# ---------------- Заявки в админке ----------------

FILTERS = [
    ("check", "⏳ Ждут проверки оплаты"),
    ("paid", "🔄 В процессе"),
    ("new", "🆕 Не оплачены"),
    ("done", "🏁 Выполнены"),
    ("rejected", "❌ Отклонены"),
    ("all", "📋 Все заявки"),
]
PAGE_SIZE = 8


def count_orders(status: str) -> int:
    with db() as conn:
        if status == "all":
            return conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM orders WHERE status = ?", (status,)).fetchone()[0]


def orders_filters():
    rows = [[InlineKeyboardButton(text=f"{name} ({count_orders(st)})", callback_data=f"list:{st}:0")]
            for st, name in FILTERS]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")])
    return "📋 Заявки — выберите раздел:", kb(rows)


def orders_list(status: str, page: int):
    with db() as conn:
        if status == "all":
            orders = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ? OFFSET ?",
                                  (PAGE_SIZE, page * PAGE_SIZE)).fetchall()
        else:
            orders = conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                (status, PAGE_SIZE, page * PAGE_SIZE)).fetchall()
    total = count_orders(status)
    title = dict(FILTERS)[status]
    if not orders:
        text = f"{title}\n\nЗдесь пока пусто."
    else:
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        text = f"{title} — {total} шт. (стр. {page + 1}/{pages})\n\nНажмите на заявку, чтобы открыть её."
    rows = []
    for o in orders:
        icon = STATUS_NAMES.get(o["status"], "").split(" ")[0]
        rows.append([InlineKeyboardButton(
            text=f"{icon} №{o['id']} · {o['niche'][:18]} · {o['total']} ₽",
            callback_data=f"ord:{o['id']}:{status}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"list:{status}:{page - 1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"list:{status}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К разделам", callback_data="adm:orders_edit")])
    return text, kb(rows)


def order_card(order_id: int, back: str = "all:0"):
    o = get_order(order_id)
    if not o:
        return "Заявка не найдена.", kb([[InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:orders_edit")]])
    who = f"@{o['username']}" if o["username"] else f"id {o['user_id']}"
    text = (
        f"📄 Заявка №{o['id']}\n\n"
        f"Создана: {o['created']}\n"
        f"Клиент: {who}\n"
        f"Ниша: {o['niche']}\n"
        f"Город: {o['city']}\n"
        f"Количество: {o['qty']} шт.\n"
        f"Сумма: {o['total']} ₽\n\n"
        f"Статус: {STATUS_NAMES.get(o['status'], o['status'])}"
    )
    rows = []
    st = o["status"]
    if st in ("check", "new", "rejected"):
        rows.append([InlineKeyboardButton(text="✅ Оплата принята", callback_data=f"ok:{o['id']}:c")])
    if st in ("check", "new"):
        rows.append([InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no:{o['id']}:c")])
    if st == "paid":
        rows.append([InlineKeyboardButton(text="🏁 Заказ выполнен", callback_data=f"done:{o['id']}:c")])
    if o["username"]:
        rows.append([InlineKeyboardButton(text="💬 Написать клиенту", url=f"https://t.me/{o['username']}")])
    st_back, page_back = back.split(":")
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data=f"list:{st_back}:{page_back}")])
    return text, kb(rows)


@router.callback_query(F.data.startswith("list:"))
async def cb_list(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    _, status, page = c.data.split(":")
    text, markup = orders_list(status, int(page))
    await c.message.edit_text(text, reply_markup=markup)
    await c.answer()


@router.callback_query(F.data.startswith("ord:"))
async def cb_order(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    _, order_id, status, page = c.data.split(":")
    text, markup = order_card(int(order_id), f"{status}:{page}")
    await c.message.edit_text(text, reply_markup=markup)
    await c.answer()


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

    if action == "orders_edit":
        text, markup = orders_filters()
        await c.message.edit_text(text, reply_markup=markup)
        await c.answer()
        return

    if action == "home":
        await c.message.answer("⚙️ Админ-панель", reply_markup=admin_menu())

    elif action == "settings":
        await c.message.answer("Что изменить?", reply_markup=settings_menu())

    elif action == "orders":
        await c.message.answer(*orders_filters())

    elif action == "reviews":
        text, markup = admin_reviews(0)
        await c.message.answer(text, reply_markup=markup)

    elif action == "stats":
        with db() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            paid = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(total), 0) FROM orders WHERE status IN ('paid', 'done')"
            ).fetchone()
        await c.message.answer(
            f"📊 Статистика\n\nПользователей: {users}\nЗаявок всего: {orders}\n"
            f"Оплачено заявок: {paid[0]}\nВыручка: {paid[1]} ₽",
            reply_markup=admin_menu(),
        )
    await c.answer()


ADMIN_REV_PAGE = 5


def admin_reviews(page: int):
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        revs = conn.execute("SELECT * FROM reviews ORDER BY id DESC LIMIT ? OFFSET ?",
                            (ADMIN_REV_PAGE, page * ADMIN_REV_PAGE)).fetchall()
    if not total:
        return "Отзывов пока нет.", admin_menu()
    if not revs and page > 0:
        return admin_reviews(page - 1)
    lines, rows = [], []
    for r in revs:
        who = f"@{r['username']}" if r["username"] else r["name"]
        lines.append(f"№{r['id']} {stars_str(r['stars'])} {who} · {r['created'][:10]}\n{r['text'] or '—'}")
        rows.append([InlineKeyboardButton(text=f"🗑 Удалить №{r['id']}",
                                          callback_data=f"rdel:{r['id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"arev:{page - 1}"))
    if (page + 1) * ADMIN_REV_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"arev:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:home")])
    return f"⭐ Отзывы — всего {total}\n\n" + "\n\n".join(lines), kb(rows)


@router.callback_query(F.data.startswith("arev:"))
async def cb_arev(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    text, markup = admin_reviews(int(c.data.split(":")[1]))
    await c.message.edit_text(text, reply_markup=markup)
    await c.answer()


@router.callback_query(F.data.startswith("rdel:"))
async def cb_rdel(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    parts = c.data.split(":")
    rid = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    if len(parts) < 4:  # первый шаг — подтверждение
        await c.message.edit_reply_markup(reply_markup=kb([
            [InlineKeyboardButton(text=f"✅ Да, удалить №{rid}", callback_data=f"rdel:{rid}:{page}:y")],
            [InlineKeyboardButton(text="↩️ Отмена", callback_data=f"arev:{page}")],
        ]))
        await c.answer()
        return
    with db() as conn:
        conn.execute("DELETE FROM reviews WHERE id = ?", (rid,))
    text, markup = admin_reviews(page)
    await c.message.edit_text(text, reply_markup=markup)
    await c.answer(f"Отзыв №{rid} удалён")


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
    if kind == "url" and (not value.startswith(("http://", "https://")) or " " in value):
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
    parts = c.data.split(":")
    action, order_id = parts[0], int(parts[1])
    from_card = len(parts) > 2
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
    if from_card:
        text, markup = order_card(order_id)
        await c.message.edit_text(text, reply_markup=markup)
    else:
        markup = None
        if status == "paid":
            markup = kb([[InlineKeyboardButton(text="🏁 Заказ выполнен", callback_data=f"done:{order_id}")]])
        await c.message.edit_text(c.message.text + f"\n\nСтатус: {STATUS_NAMES[status]}",
                                  reply_markup=markup)
    await c.answer("Готово")


@router.callback_query(F.data.startswith("done:"))
async def cb_done(c: CallbackQuery, bot: Bot):
    if not is_admin(c.from_user.id):
        await c.answer()
        return
    parts = c.data.split(":")
    order_id = int(parts[1])
    from_card = len(parts) > 2
    order = get_order(order_id)
    if not order:
        await c.answer("Заявка не найдена", show_alert=True)
        return
    if order["status"] == "done":
        await c.answer("Уже отмечен как выполненный", show_alert=True)
        return
    set_order_status(order_id, "done")
    try:
        await bot.send_message(order["user_id"], fmt("done_text", order_id=order_id))
    except Exception as e:
        logging.warning("Не удалось написать клиенту: %s", e)
    if from_card:
        text, markup = order_card(order_id)
        await c.message.edit_text(text, reply_markup=markup)
    else:
        await c.message.edit_reply_markup(reply_markup=None)
        await c.message.answer(f"🏁 Заказ №{order_id} отмечен выполненным, клиент уведомлён.")
    await c.answer("Готово")


@router.message(StateFilter(None))
async def fallback(m: Message):
    """Сообщение вне диалога — раньше бот молчал, теперь подсказывает."""
    await m.answer("Не понял сообщение. Выберите раздел в меню ниже 👇\n"
                   "Если вы оформляли заказ — нажмите «Купить» и начните заново.",
                   reply_markup=reply_menu())


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
    logging.info("База данных: %s", DB_PATH)
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=SQLiteStorage())
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="Главное меню")])
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
