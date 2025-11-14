#!/usr/bin/env python3
# main.py — OBMIN24 - #МіняйВигідно (all-in-one)
# Python 3.11, aiogram 3.x, webhook server via aiohttp.
# Usage: fill .env and run. Deploy to Render as Web Service (binds $PORT).

import os
import asyncio
import uuid
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage

# ---------------------------
# Load config
# ---------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # e.g. https://your-app.onrender.com/webhook
TON_API_KEY = os.getenv("TON_API_KEY")  # optional but recommended
TON_API_URL = os.getenv("TON_API_URL")  # provider URL, e.g. toncenter or tonapi base URL
MERCHANT_WALLET = os.getenv("MERCHANT_WALLET")  # address that receives TON
PORT = int(os.getenv("PORT", "8000"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))  # seconds between checks
STATS_FILE = os.getenv("STATS_FILE", "stats.json")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN is required in .env")
if not MERCHANT_WALLET:
    raise SystemExit("MERCHANT_WALLET is required in .env")
if not TON_API_URL:
    # we can still run, but TON checks will likely fail -> warn
    print("WARNING: TON_API_URL not set — TON checks may not work properly.")

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("obmin24")

# ---------------------------
# Bot init
# ---------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ---------------------------
# Simple multilingual helper
# ---------------------------
MESSAGES = {
    "welcome": {
        "ru": "Добро пожаловать в OBMIN24 — #МіняйВигідно!\nСоздать сделку: /new\nМои сделки: /mydeals\nМоя статистика: /mystats",
        "ua": "Ласкаво просимо в OBMIN24 — #МіняйВигідно!\nСтворити угоду: /new\nМої угоди: /mydeals\nМоя статистика: /mystats",
        "en": "Welcome to OBMIN24 — #МіняйВигідно!\nCreate deal: /new\nMy deals: /mydeals\nMy stats: /mystats",
    },
    "ask_desc": {
        "ru": "Опиши, что ты отдаёшь (подарки/NFT/описание).",
        "ua": "Опис, що ви віддаєте (подарунки/NFT/опис).",
        "en": "Describe what you're selling (gifts/NFT/description).",
    },
    "ask_amount": {
        "ru": "Укажи сумму в TON (например: 10.5).",
        "ua": "Вкажіть суму в TON (наприклад: 10.5).",
        "en": "Enter amount in TON (e.g. 10.5).",
    },
    "deal_created": {
        "ru": "Сделка создана! ID: {id}\nСумма: {amount} TON\nОписание: {desc}\nОтправьте оплату на адрес:\n{wallet}\nMemo (рекомендуется): {memo}\nКак только TON поступят — сделка автоматически закроется.",
        "ua": "Угода створена! ID: {id}\nСума: {amount} TON\nОпис: {desc}\nНадішліть оплату на адресу:\n{wallet}\nMemo (рекомендується): {memo}\nЯк тільки TON надійдуть — угода автоматично закриється.",
        "en": "Deal created! ID: {id}\nAmount: {amount} TON\nDescription: {desc}\nSend payment to:\n{wallet}\nMemo (recommended): {memo}\nOnce TON arrive — deal will be closed automatically.",
    },
    "no_active": {
        "ru": "У вас нет активных сделок.",
        "ua": "У вас немає активних угод.",
        "en": "You have no active deals.",
    },
    "deal_closed": {
        "ru": "Сделка {id} закрыта — средства получены (tx {tx}). Спасибо! 🎉",
        "ua": "Угода {id} закрита — кошти отримано (tx {tx}). Дякуємо! 🎉",
        "en": "Deal {id} closed — funds received (tx {tx}). Thank you! 🎉",
    },
    "mystats": {
        "ru": "Успешных сделок: {n}",
        "ua": "Успішних угод: {n}",
        "en": "Successful deals: {n}",
    }
}

def lang_for(message: types.Message) -> str:
    code = (getattr(message.from_user, "language_code", "") or "").lower()
    if code.startswith("uk") or code.startswith("ukr") or code.startswith("ua"):
        return "ua"
    if code.startswith("en"):
        return "en"
    return "ru"

def t(key: str, message: types.Message, **kwargs) -> str:
    language = lang_for(message)
    template = MESSAGES.get(key, {}).get(language) or MESSAGES.get(key, {}).get("ru") or ""
    return template.format(**kwargs)

# ---------------------------
# In-memory deals store (no persistent deal storage)
# Structure: deals[deal_id] = { creator_id, amount, description, memo, created_unix }
# ---------------------------
deals: Dict[str, Dict[str, Any]] = {}

# ---------------------------
# Stats persistence (only successful deals count per user)
# stored in JSON file: STATS_FILE
# ---------------------------
_stats_lock = asyncio.Lock()
def _load_stats_sync() -> Dict[str, int]:
    if not os.path.exists(STATS_FILE):
        return {}
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        log.exception("Failed to load stats file; resetting")
        return {}

def _save_stats_sync(data: Dict[str, int]) -> None:
    tmp = STATS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATS_FILE)

async def get_stats() -> Dict[str, int]:
    return await asyncio.to_thread(_load_stats_sync)

async def increment_user_success(user_id: int) -> None:
    async with _stats_lock:
        data = await asyncio.to_thread(_load_stats_sync)
        key = str(user_id)
        data[key] = int(data.get(key, 0)) + 1
        await asyncio.to_thread(_save_stats_sync, data)

async def get_user_success(user_id: int) -> int:
    data = await asyncio.to_thread(_load_stats_sync)
    return int(data.get(str(user_id), 0))

# ---------------------------
# FSM for creating deals
# ---------------------------
class NewDealStates(StatesGroup):
    waiting_description = State()
    waiting_amount = State()

# ---------------------------
# Ton provider helpers
# ---------------------------
def safe_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        if isinstance(x, dict):
            if "amount" in x:
                return float(x["amount"])
            return None
        return float(x)
    except Exception:
        try:
            return float(str(x))
        except Exception:
            return None

def to_ton(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except Exception:
        return None
    # heuristic: if large (>1e6) it's probably nanotons/atoms -> divide
    if v > 1e6:
        return v / 1e9
    return v

async def fetch_transactions_for_address(session: aiohttp.ClientSession, address: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Flexible fetcher. Tries tonapi style and toncenter style.
    Returns raw txs list.
    """
    if not TON_API_URL:
        return []

    headers = {}
    if TON_API_KEY:
        headers["Authorization"] = f"Bearer {TON_API_KEY}"
        # some providers expect X-API-Key
        headers["X-API-Key"] = TON_API_KEY

    # try several shapes
    try:
        # toncenter pattern
        if "toncenter" in TON_API_URL:
            params = {"account": address, "limit": limit}
            async with session.get(TON_API_URL, params=params, headers={"X-API-Key": TON_API_KEY} if TON_API_KEY else {}, timeout=20) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and "result" in data:
                        return data["result"]
        # tonapi pattern
        if "tonapi" in TON_API_URL:
            # tonapi often: /v2/accounts/{address}/transactions
            url = TON_API_URL.rstrip("/") + f"/{address}/transactions?limit={limit}"
            async with session.get(url, headers=headers, timeout=20) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict) and "transactions" in data:
                        return data["transactions"]
        # fallback try base_url/{address}/transactions
        try:
            url = TON_API_URL.rstrip("/") + f"/{address}/transactions?limit={limit}"
            async with session.get(url, headers=headers, timeout=20) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, dict):
                        if "transactions" in data:
                            return data["transactions"]
                        if "result" in data and isinstance(data["result"], list):
                            return data["result"]
                    if isinstance(data, list):
                        return data
        except Exception:
            pass
        # last fallback: try GET TON_API_URL directly
        async with session.get(TON_API_URL, headers=headers, timeout=20) as resp:
            if resp.status == 200:
                data = await resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    # pick first list we find
                    for v in data.values():
                        if isinstance(v, list):
                            return v
    except Exception:
        log.exception("fetch_transactions_for_address error")
    return []

async def normalize_transactions(raw_txs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in raw_txs:
        try:
            # find unix time
            unix_time = None
            for k in ("utime", "time", "unix_time"):
                if it.get(k):
                    try:
                        unix_time = int(it.get(k))
                        break
                    except Exception:
                        pass
            tx_hash = it.get("hash") or it.get("transaction_id") or it.get("id")
            # amount heuristics
            amount = None
            if isinstance(it.get("in_msg"), dict) and it["in_msg"].get("value"):
                amount = to_ton(safe_float(it["in_msg"].get("value")))
            elif isinstance(it.get("out_msgs"), list) and it["out_msgs"]:
                v = it["out_msgs"][0].get("value") or it["out_msgs"][0].get("amount")
                amount = to_ton(safe_float(v))
            else:
                amount = to_ton(safe_float(it.get("value") or it.get("amount")))

            # from/to
            _from = None
            _to = None
            if isinstance(it.get("in_msg"), dict):
                _from = it["in_msg"].get("source") or it["in_msg"].get("from")
            _from = _from or it.get("src") or it.get("source") or it.get("from")
            if isinstance(it.get("out_msgs"), list) and it["out_msgs"]:
                _to = it["out_msgs"][0].get("destination") or it["out_msgs"][0].get("to") or _to
            _to = _to or it.get("dst") or it.get("destination") or it.get("to")

            # message/comment
            msg = None
            if isinstance(it.get("in_msg"), dict):
                msg = it["in_msg"].get("text") or it["in_msg"].get("message") or it["in_msg"].get("comment")
            if not msg:
                msg = it.get("comment") or it.get("message") or it.get("payload") or it.get("body")

            out.append({
                "hash": str(tx_hash) if tx_hash else None,
                "from": _from,
                "to": _to,
                "amount": amount,
                "message": msg,
                "unix_time": unix_time,
                "raw": it
            })
        except Exception:
            log.exception("normalize tx failed for: %s", it)
            continue
    return out

# ---------------------------
# Periodic checker task
# ---------------------------
async def periodic_checker(app):
    log.info("Periodic checker started")
    await asyncio.sleep(2)  # small startup delay
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                if not deals:
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                # fetch txs to merchant wallet
                raw = await fetch_transactions_for_address(session, MERCHANT_WALLET, limit=80)
                normalized = await normalize_transactions(raw)
                # iterate copy of deals to allow removal
                for deal_id, deal in list(deals.items()):
                    matched = None
                    # try memo match first
                    for tx in normalized:
                        msg = tx.get("message")
                        if isinstance(msg, bytes):
                            try:
                                msg = msg.decode(errors="ignore")
                            except Exception:
                                msg = str(msg)
                        if msg and isinstance(msg, str) and deal_id in msg:
                            matched = tx
                            break
                    # fallback: amount + time
                    if not matched:
                        try:
                            created_dt = datetime.fromisoformat(deal["created_iso"])
                            created_unix = int(created_dt.replace(tzinfo=timezone.utc).timestamp())
                        except Exception:
                            created_unix = 0
                        for tx in normalized:
                            amt = tx.get("amount")
                            tx_time = tx.get("unix_time") or 0
                            if amt is not None and amt >= float(deal["amount"]) and tx_time and tx_time >= created_unix:
                                matched = tx
                                break
                    # final fallback: any tx >= amount
                    if not matched:
                        for tx in normalized:
                            amt = tx.get("amount")
                            if amt is not None and amt >= float(deal["amount"]):
                                matched = tx
                                break

                    if matched:
                        log.info("Deal matched: %s -> tx %s amount %s", deal_id, matched.get("hash"), matched.get("amount"))
                        # complete deal: notify, increment user stat, remove in-memory deal
                        try:
                            # remove before notifications to avoid double match
                            deals.pop(deal_id, None)
                            await increment_user_success(deal["creator_id"])
                            # notify seller
                            try:
                                await bot.send_message(deal["creator_id"], t("deal_closed", types.SimpleNamespace(from_user=types.User(id=deal["creator_id"], is_bot=False, first_name="User", language_code="ru")), id=deal_id, tx=matched.get("hash")))
                            except Exception:
                                # fallback language detection by constructing fake message
                                await bot.send_message(deal["creator_id"], f"Deal {deal_id} closed — tx {matched.get('hash')}")
                        except Exception:
                            log.exception("Error completing deal %s", deal_id)
                await asyncio.sleep(POLL_INTERVAL)
            except Exception:
                log.exception("Error in periodic_checker")
                await asyncio.sleep(5)

# ---------------------------
# Handlers
# ---------------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(t("welcome", message))

@dp.message(Command("new"))
async def cmd_new(message: types.Message, state: FSMContext):
    await state.set_state(NewDealStates.waiting_description)
    await message.answer(t("ask_desc", message))

@dp.message(NewDealStates.waiting_description)
async def desc_handler(message: types.Message, state: FSMContext):
    desc = message.text.strip()
    await state.update_data(description=desc)
    await state.set_state(NewDealStates.waiting_amount)
    await message.answer(t("ask_amount", message))

@dp.message(NewDealStates.waiting_amount)
async def amount_handler(message: types.Message, state: FSMContext):
    text = message.text.strip().replace(",", ".")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError()
    except Exception:
        await message.answer(t("ask_amount", message))
        return
    data = await state.get_data()
    desc = data.get("description", "")
    seller = message.from_user.id
    deal_id = uuid.uuid4().hex
    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    # store in-memory
    deals[deal_id] = {
        "id": deal_id,
        "creator_id": seller,
        "amount": amount,
        "description": desc,
        "memo": deal_id,
        "created_iso": now_iso,
    }
    # notify user with payment details
    await message.answer(t("deal_created", message, id=deal_id, amount=amount, desc=desc or "-", wallet=MERCHANT_WALLET, memo=deal_id), parse_mode="Markdown")
    # clear state
    await state.clear()

@dp.message(Command("mydeals"))
async def cmd_mydeals(message: types.Message):
    uid = message.from_user.id
    own = [d for d in deals.values() if d["creator_id"] == uid]
    if not own:
        await message.answer(t("no_active", message))
        return
    out = "Ваши активные сделки:\n\n" if lang_for(message) != "en" else "Your active deals:\n\n"
    for d in own:
        out += f"#{d['id'][:8]} — {d['amount']} TON — {d['description'][:80]} — створена {d['created_iso']}\n"
    await message.answer(out)

@dp.message(Command("mystats"))
async def cmd_mystats(message: types.Message):
    n = await get_user_success(message.from_user.id)
    await message.answer(t("mystats", message, n=n))

# fallback
@dp.message()
async def fallback(message: types.Message):
    # keep minimal replies
    await message.answer(t("welcome", message))

# ---------------------------
# Webhook app & lifecycle
# ---------------------------
async def on_startup(app: web.Application):
    log.info("Starting up: setting webhook and launching checker")
    # set webhook for bot
    if WEBHOOK_URL:
        try:
            await bot.set_webhook(WEBHOOK_URL)
            log.info("Webhook set to %s", WEBHOOK_URL)
        except Exception:
            log.exception("set_webhook failed")
    # start periodic checker
    app["checker"] = asyncio.create_task(periodic_checker(app))

async def on_shutdown(app: web.Application):
    log.info("Shutting down")
    task = app.get("checker")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    try:
        await bot.delete_webhook()
    except Exception:
        pass
    await bot.session.close()

def create_app():
    # minimal web app: webhook + health
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    async def health(request):
        return web.Response(text="ok")

    app.router.add_get("/", health)
    return app

# ---------------------------
# Run
# ---------------------------
if __name__ == "__main__":
    app = create_app()
    log.info("OBMIN24 starting on port %s", PORT)
    web.run_app(app, port=PORT)
