# main.py
import os
import asyncio
import uuid
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import aiohttp
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.bot import DefaultBotProperties

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
TON_API_KEY = os.getenv("TON_API_KEY")        # optional but recommended
TON_API_URL = os.getenv("TON_API_URL")        # required to fetch txs (e.g. toncenter/tonapi)
MERCHANT_WALLET = os.getenv("MERCHANT_WALLET")  # адрес, куда Trustify/конвертер кладёт TON
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "12"))  # seconds between checks

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN not found in .env")
if not MERCHANT_WALLET:
    raise SystemExit("MERCHANT_WALLET not found in .env — укажи адрес для поступлений.")
if not TON_API_URL:
    print("WARNING: TON_API_URL not found in .env — укажи URL провайдера (toncenter/tonapi/etc).")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

DB_PATH = "exchange.db"

# -------------------------
# DB helpers
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS deals (
        id TEXT PRIMARY KEY,
        creator_id INTEGER,
        wallet TEXT,
        amount REAL,
        description TEXT,
        memo TEXT,
        status TEXT,
        created_at TEXT,
        paid_tx TEXT,
        buyer_id INTEGER,
        nft_received INTEGER DEFAULT 0,
        seller_received INTEGER DEFAULT 0,
        completed_at TEXT
    );
    """)
    conn.commit()
    conn.close()

def create_user_if_not_exists(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)", (user_id, 0.0))
    conn.commit()
    conn.close()

def get_user_balance(user_id: int) -> float:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0.0

def change_user_balance(user_id: int, delta: float):
    create_user_if_not_exists(user_id)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (delta, user_id))
    conn.commit()
    conn.close()

def save_deal_to_db(deal: Dict[str, Any]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO deals (id, creator_id, wallet, amount, description, memo, status, created_at, paid_tx, buyer_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (deal["id"], deal["creator_id"], deal["wallet"], deal["amount"], deal["description"],
          deal["memo"], deal["status"], deal["created_at"], deal.get("paid_tx"), deal.get("buyer_id")))
    conn.commit()
    conn.close()

def update_deal_status(deal_id: str, status: str, paid_tx: Optional[str] = None,
                       buyer_id: Optional[int] = None, nft_received: Optional[bool]=None,
                       seller_received: Optional[bool]=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    fields = ["status = ?"]
    params = [status]
    if paid_tx is not None:
        fields.append("paid_tx = ?")
        params.append(paid_tx)
    if buyer_id is not None:
        fields.append("buyer_id = ?")
        params.append(buyer_id)
    if nft_received is not None:
        fields.append("nft_received = ?")
        params.append(1 if nft_received else 0)
    if seller_received is not None:
        fields.append("seller_received = ?")
        params.append(1 if seller_received else 0)
    # completed_at if status is completed
    if status in ("completed", "done"):
        fields.append("completed_at = ?")
        params.append(datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())
    params.append(deal_id)
    sql = f"UPDATE deals SET {', '.join(fields)} WHERE id = ?"
    cur.execute(sql, tuple(params))
    conn.commit()
    conn.close()

def get_deal_from_db(deal_id: str) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, creator_id, wallet, amount, description, memo, status, created_at, paid_tx, buyer_id, nft_received, seller_received, completed_at FROM deals WHERE id = ?", (deal_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    keys = ["id","creator_id","wallet","amount","description","memo","status","created_at","paid_tx","buyer_id","nft_received","seller_received","completed_at"]
    d = dict(zip(keys, row))
    # convert ints to bools
    d["nft_received"] = bool(d["nft_received"])
    d["seller_received"] = bool(d["seller_received"])
    return d

def list_user_deals(user_id: int) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, amount, status, created_at FROM deals WHERE creator_id = ? ORDER BY created_at DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"id":r[0],"amount":r[1],"status":r[2],"created_at":r[3]} for r in rows]

def list_waiting_deals() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, creator_id, wallet, amount, description, memo, status, created_at FROM deals WHERE status = 'waiting_nft' OR status = 'waiting_payment'")
    rows = cur.fetchall()
    conn.close()
    keys = ["id","creator_id","wallet","amount","description","memo","status","created_at"]
    return [dict(zip(keys, r)) for r in rows]

# -------------------------
# FSM
# -------------------------
class DealStates(StatesGroup):
    waiting_amount = State()
    waiting_description = State()

# -------------------------
# UI helpers
# -------------------------
def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Новая сделка")],
            [KeyboardButton(text="Мои сделки")],
            [KeyboardButton(text="Баланс")],
        ],
        resize_keyboard=True
    )

def deal_buttons_for_buyer(deal_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатил (платёж с внешнего кошелька)", callback_data=f"paid_ext:{deal_id}")],
        [InlineKeyboardButton(text="Оплатить с баланса", callback_data=f"pay_bal:{deal_id}")],
        [InlineKeyboardButton(text="Вернуться в меню", callback_data="back_menu")]
    ])

def deal_buttons_for_creator(deal_id: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Отметить как получено (подтвердить)", callback_data=f"confirm:{deal_id}")],
        [InlineKeyboardButton(text="Отменить", callback_data=f"cancel:{deal_id}")],
    ])

# -------------------------
# TON checking helper (robust parser)
# -------------------------
async def fetch_transactions_for_address(session: aiohttp.ClientSession, address: str) -> List[Dict[str, Any]]:
    """
    Fetch recent transactions for `address` using TON provider (TON_API_URL).
    This parser tries to be flexible to support toncenter/tonapi shapes.
    Returns list of normalized transactions:
    { "hash": ..., "from": ..., "to": ..., "amount": float_or_None, "message": str_or_None, "unix_time": int_or_None }
    """
    if not TON_API_URL:
        return []

    headers = {}
    params = {}
    params = {"account": address, "limit": 50}
    headers = {"X-API-Key": TON_API_KEY} if TON_API_KEY else {}

    try:
        async with session.get(TON_API_URL, params=params, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                # try without params (some providers require different)
                try:
                    data = await resp.json()
                except Exception:
                    return []
            else:
                data = await resp.json()
    except Exception:
        return []

    txs = []
    items = []
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], list):
        items = data["result"]
    elif isinstance(data, dict) and "transactions" in data and isinstance(data["transactions"], list):
        items = data["transactions"]
    elif isinstance(data, list):
        items = data
    else:
        # try other common wrappers
        if isinstance(data, dict):
            for k,v in data.items():
                if isinstance(v, list):
                    items = v
                    break

    for it in items:
        try:
            # attempt common extractions
            tx_hash = it.get("hash") or it.get("utime") or it.get("lt") or it.get("id") or it.get("transaction_id")
            # from/to extraction — various shapes
            sender = None
            recipient = None
            if isinstance(it.get("in_msg"), dict):
                sender = it["in_msg"].get("source") or it["in_msg"].get("from")
                # sometimes message includes value
                amt = it["in_msg"].get("value")
            else:
                sender = it.get("src") or it.get("source") or it.get("from")
                amt = it.get("value") or it.get("amount")
            # out_msgs array sometimes contains destination and value
            if isinstance(it.get("out_msgs"), list) and it.get("out_msgs"):
                recipient = it["out_msgs"][0].get("destination") or it["out_msgs"][0].get("to") or recipient
                if amt is None:
                    amt = it["out_msgs"][0].get("value") or it["out_msgs"][0].get("amount")
            # message/comment
            msg = None
            if isinstance(it.get("in_msg"), dict):
                msg = it["in_msg"].get("text") or it["in_msg"].get("message") or it["in_msg"].get("comment")
            if not msg:
                msg = it.get("comment") or it.get("message") or it.get("body") or it.get("payload")
            unix_time = it.get("utime") or it.get("time") or it.get("unix_time") or None
            # normalize amount
            amount_val = None
            try:
                if amt is not None:
                    amount_val = float(amt)
            except Exception:
                try:
                    # sometimes value is dict
                    amount_val = float((amt or {}).get("amount", 0))
                except Exception:
                    amount_val = None
            normalized = {
                "hash": str(tx_hash) if tx_hash is not None else None,
                "from": sender,
                "to": recipient,
                "amount": amount_val,
                "message": msg,
                "unix_time": int(unix_time) if unix_time else None,
                "raw": it
            }
            txs.append(normalized)
        except Exception:
            continue

    return txs

async def check_payment_for_deal(session: aiohttp.ClientSession, deal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Tries to find a transaction that pays for `deal`.
    Matching strategies (in order):
    1) tx.message contains deal_id (memo match)
    2) tx.amount >= deal.amount AND tx.unix_time >= deal.created_at
    3) fallback: any tx with amount >= deal.amount
    Returns tx dict if found.
    """
    txs = await fetch_transactions_for_address(session, MERCHANT_WALLET)
    # parse created_at into unix seconds
    try:
        created_dt = datetime.fromisoformat(deal["created_at"])
        created_unix = int(created_dt.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        created_unix = 0

    for tx in txs:
        # message might be bytes
        msg = tx.get("message")
        if isinstance(msg, bytes):
            try:
                msg = msg.decode(errors="ignore")
            except Exception:
                msg = str(msg)
        if isinstance(msg, str) and deal["id"] in msg:
            # prefer memo match
            return tx

    # fallback: try amount+time
    for tx in txs:
        amt = tx.get("amount")
        tx_time = tx.get("unix_time") or 0
        try:
            if amt is not None and float(amt) >= float(deal["amount"]) and tx_time and tx_time >= created_unix:
                return tx
        except Exception:
            continue

    # last fallback: any tx with amount >= amount (ignores time)
    for tx in txs:
        amt = tx.get("amount")
        try:
            if amt is not None and float(amt) >= float(deal["amount"]):
                return tx
        except Exception:
            continue

    return None

# -------------------------
# Background periodic checker
# -------------------------
async def periodic_ton_checker():
    await asyncio.sleep(2)  # initial delay
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                waiting = list_waiting_deals()
                if not waiting:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                for deal in waiting:
                    # we only treat deals that are waiting for NFT/payment
                    if deal["status"] not in ("waiting_nft", "waiting_payment"):
                        continue
                    tx = await check_payment_for_deal(session, deal)
                    if tx:
                        # mark deal as paid/completed
                        try:
                            update_deal_status(deal["id"], "completed", paid_tx=tx.get("hash"), buyer_id=None, nft_received=True, seller_received=True)
                            # credit seller balance (optional)
                            try:
                                change_user_balance(deal["creator_id"], float(deal["amount"]))
                            except Exception:
                                pass
                            # notify seller
                            try:
                                asyncio.create_task(bot.send_message(deal["creator_id"],
                                    f"Сделка {deal['id'][:8]}: поступление TON обнаружено (tx: {tx.get('hash')}). Сделка автоматически закрыта. Баланс пополнен на {deal['amount']} TON."))
                            except Exception:
                                pass
                        except Exception:
                            pass
                await asyncio.sleep(POLL_INTERVAL)
            except Exception:
                await asyncio.sleep(5)

# -------------------------
# Handlers
# -------------------------
init_db()

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    create_user_if_not_exists(message.from_user.id)
    args = message.text.split()
    if len(args) == 1:
        await message.answer("Привет! Это обменник. Выбери действие:", reply_markup=main_menu())
        return

    # /start <deal_id> — buyer opens deal (if someone clicks link)
    deal_id = args[1].strip()
    deal = get_deal_from_db(deal_id)
    if not deal:
        await message.answer("Сделка не найдена или уже завершена.")
        return

    seller_chat = None
    try:
        seller_chat = await bot.get_chat(deal['creator_id'])
        seller_info = f"@{seller_chat.username}" if getattr(seller_chat, "username", None) else str(deal['creator_id'])
    except Exception:
        seller_info = str(deal['creator_id'])

    text = (
        f"💳 Информация о сделке #{deal['id']}\n\n"
        f"👤 Продавец: {seller_info}\n"
        f"• Описание: {deal['description']}\n\n"
        f"💰 Сумма к зачислению: {deal['amount']} TON\n"
        f"📝 Memo (id): {deal['memo']}\n\n"
        f"⚠️ Убедитесь в правильности данных. Сделка автоматически закроется при поступлении средств на кошелёк."
    )
    await message.answer(text, reply_markup=deal_buttons_for_buyer(deal_id))

# universal menu (only when no FSM active)
@dp.message(StateFilter(None), F.text)
async def menu_router(message: types.Message, state: FSMContext):
    text = message.text.strip()
    uid = message.from_user.id
    create_user_if_not_exists(uid)

    if text == "Новая сделка":
        await message.answer("Введите описание товара/услуги (например: 10 Кепок и Пепочка):")
        await state.set_state(DealStates.waiting_description)
        return

    if text == "Мои сделки":
        rows = list_user_deals(uid)
        if not rows:
            await message.answer("У вас нет сделок.", reply_markup=main_menu())
            return
        out = "Ваши сделки:\n\n"
        for r in rows:
            out += f"#{r['id'][:8]} — {r['amount']} TON — {r['status']} — {r['created_at']}\n"
        await message.answer(out, reply_markup=main_menu())
        return

    if text == "Баланс":
        bal = get_user_balance(uid)
        await message.answer(f"Ваш баланс: {bal} TON", reply_markup=main_menu())
        return

    await message.answer("Неизвестная команда. Используй клавиатуру.", reply_markup=main_menu())

# FSM: description -> amount -> create deal
@dp.message(DealStates.waiting_description)
async def receive_description(message: types.Message, state: FSMContext):
    desc = message.text.strip()
    await state.update_data(description=desc)
    await message.answer("Введите сумму в TON (например: 100.5):")
    await state.set_state(DealStates.waiting_amount)

@dp.message(DealStates.waiting_amount)
async def receive_amount(message: types.Message, state: FSMContext):
    text = message.text.strip()
    try:
        amount = float(text)
    except Exception:
        await message.answer("Неверный формат суммы. Введите число, например: 100.5")
        return
    data = await state.get_data()
    desc = data.get("description")
    seller = message.from_user.id
    memo = uuid.uuid4().hex
    deal_id = memo  # using memo as unique id
    deal = {
        "id": deal_id,
        "creator_id": seller,
        "wallet": MERCHANT_WALLET,
        "amount": amount,
        "description": desc,
        "memo": memo,
        "status": "waiting_nft",
        "created_at": datetime.utcnow().replace(tzinfo=timezone.utc).isoformat(),
        "paid_tx": None,
        "buyer_id": None
    }
    save_deal_to_db(deal)
    link = f"https://t.me/{(await bot.get_me()).username}?start={deal_id}"
    await message.answer(
        f"Сделка создана!\n\n"
        f"Описание: {desc}\n"
        f"Сумма: {amount} TON\n"
        f"Memo (id): {memo}\n\n"
        f"Ссылка для покупателя:\n{link}\n\n"
        f"Отправляйте подарок/NFT/конвертацию — как только TON поступит на кошелёк, сделка закроется автоматически.",
        reply_markup=main_menu()
    )
    await state.clear()

# Callback handlers (kept but many manual steps are no longer needed in automatic flow)
@dp.callback_query(F.data.startswith("paid_ext:"))
async def cb_paid_ext(cb: types.CallbackQuery):
    deal_id = cb.data.split(":",1)[1]
    deal = get_deal_from_db(deal_id)
    if not deal:
        await cb.answer("Сделка не найдена.", show_alert=True)
        return
    # buyer marked as paid (optional) — we still accept automatic closing via TON
    update_deal_status(deal_id, "paid_pending", paid_tx=None, buyer_id=cb.from_user.id)
    await cb.message.answer(f"Вы отметили оплату для сделки {deal_id[:8]}. Ожидайте автоматического подтверждения.")
    await cb.answer("Отмечено: ожидайте подтверждения.", show_alert=True)
    try:
        await bot.send_message(deal["creator_id"], f"Покупатель @{cb.from_user.username or cb.from_user.id} отметил оплату по сделке {deal_id[:8]}.")
    except Exception:
        pass

@dp.callback_query(F.data.startswith("pay_bal:"))
async def cb_pay_bal(cb: types.CallbackQuery):
    deal_id = cb.data.split(":",1)[1]
    deal = get_deal_from_db(deal_id)
    if not deal:
        await cb.answer("Сделка не найдена.", show_alert=True)
        return
    buyer = cb.from_user.id
    bal = get_user_balance(buyer)
    if bal < deal["amount"]:
        await cb.answer("Недостаточно баланса.", show_alert=True)
        return
    change_user_balance(buyer, -deal["amount"])
    change_user_balance(deal["creator_id"], deal["amount"])
    update_deal_status(deal_id, "completed", paid_tx="internal_balance", buyer_id=buyer, nft_received=True, seller_received=True)
    await cb.message.answer("Оплата с баланса прошла успешно. Сделка завершена.")
    await cb.answer("Оплачено с баланса.", show_alert=True)

@dp.callback_query(F.data == "back_menu")
async def cb_back_menu(cb: types.CallbackQuery):
    await cb.message.answer("Возвращаем в меню.", reply_markup=main_menu())
    await cb.answer()

@dp.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(cb: types.CallbackQuery):
    deal_id = cb.data.split(":",1)[1]
    deal = get_deal_from_db(deal_id)
    if not deal:
        await cb.answer("Сделка не найдена.", show_alert=True)
        return
    if cb.from_user.id != deal["creator_id"]:
        await cb.answer("Только продавец может подтвердить получение.", show_alert=True)
        return
    update_deal_status(deal_id, "completed", paid_tx=deal.get("paid_tx"), buyer_id=deal.get("buyer_id"), nft_received=True, seller_received=True)
    await cb.message.answer("Продавец подтвердил получение. Сделка завершена.")
    await cb.answer("Подтверждено.", show_alert=True)

@dp.callback_query(F.data.startswith("cancel:"))
async def cb_cancel(cb: types.CallbackQuery):
    deal_id = cb.data.split(":",1)[1]
    deal = get_deal_from_db(deal_id)
    if not deal:
        await cb.answer("Сделка не найдена.", show_alert=True)
        return
    if cb.from_user.id != deal["creator_id"]:
        await cb.answer("Только продавец может отменить.", show_alert=True)
        return
    update_deal_status(deal_id, "cancelled")
    await cb.message.answer("Сделка отменена.")
    await cb.answer("Отменено.", show_alert=True)

# Fallback
@dp.message()
async def fallback(message: types.Message):
    await message.answer("Используйте меню.", reply_markup=main_menu())

# -------------------------
# Run
# -------------------------
async def main():
    # start background checker
    # Use dispatcher loop to create task
    try:
        dp.loop.create_task(periodic_ton_checker())
    except Exception:
        # fallback
        asyncio.create_task(periodic_ton_checker())
    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    init_db()
    asyncio.run(main())
