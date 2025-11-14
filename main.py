import asyncio
import uuid
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

# ===================== ENV =====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ===================== Локали =====================
LANG = {
    "ru": {
        "new_deal": "Новая сделка",
        "add_wallet": "Добавить/Изменить кошелек",
        "referral": "Реферальная ссылка",
        "change_lang": "Сменить язык",
        "support": "Поддержка",
        "enter_amount": "Введите сумму. Например: 100.5",
        "enter_description": "📝 Опишите, что вы предлагаете:\nПример: 10 кепок и пепочка",
        "deal_created": "🎉 Сделка создана!",
        "wallet_missing": "Вы не указали кошелек!",
        "lang_changed": "✅ Язык изменен!"
    }
}

# ===================== FSM =====================
class DealStates(StatesGroup):
    waiting_amount = State()
    waiting_description = State()
    waiting_wallet = State()

# ===================== Хранилище =====================
user_lang = {}
user_wallet = {}

# ===================== Клавиатуры =====================
def main_menu(lang="ru"):
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=LANG[lang]["new_deal"])],
            [KeyboardButton(text=LANG[lang]["add_wallet"])],
            [KeyboardButton(text=LANG[lang]["referral"])],
            [KeyboardButton(text=LANG[lang]["change_lang"])],
            [KeyboardButton(text=LANG[lang]["support"])],
        ],
        resize_keyboard=True
    )

def lang_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🇷🇺 Русский")],
            [KeyboardButton(text="🇺🇦 Українська")],
            [KeyboardButton(text="🇬🇧 English")],
        ],
        resize_keyboard=True
    )

# ===================== START =====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_lang[message.from_user.id] = "ru"
    await message.answer("👋 Привет!", reply_markup=main_menu("ru"))

# ===================== ГЛАВНОЕ МЕНЮ =====================
@dp.message(StateFilter(None), F.text)
async def menu_router(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    lang = user_lang.get(uid, "ru")
    text = message.text

    # ===== Новая сделка =====
    if text == LANG[lang]["new_deal"]:
        if uid not in user_wallet:
            await message.answer(LANG[lang]["wallet_missing"])
            return
        await state.set_state(DealStates.waiting_amount)
        await message.answer(LANG[lang]["enter_amount"])
        return

    # ===== Добавить кошелёк =====
    if text == LANG[lang]["add_wallet"]:
        await state.set_state(DealStates.waiting_wallet)
        await message.answer("Введите ваш TON-кошелек:")
        return

    # ===== Рефералка =====
    if text == LANG[lang]["referral"]:
        await message.answer(f"https://t.me/OBMIN24_bot?start={uid}")
        return

    # ===== Поддержка =====
    if text == LANG[lang]["support"]:
        await message.answer("🆘 Поддержка: @obmin24supporter_bot")
        return

    # ===== Смена языка =====
    if text == LANG[lang]["change_lang"]:
        await message.answer("Выберите язык:", reply_markup=lang_menu())
        return

    # ===== Выбор языка =====
    if text in ["🇷🇺 Русский", "🇺🇦 Українська", "🇬🇧 English"]:
        user_lang[uid] = "ru"
        await message.answer("Язык переключен.", reply_markup=main_menu("ru"))
        return

# ===================== FSM — WALLET =====================
@dp.message(DealStates.waiting_wallet)
async def save_wallet(message: types.Message, state: FSMContext):
    user_wallet[message.from_user.id] = message.text
    await message.answer("Готово ✓", reply_markup=main_menu())
    await state.clear()

# ===================== FSM — AMOUNT =====================
@dp.message(DealStates.waiting_amount)
async def deal_amount(message: types.Message, state: FSMContext):
    await state.update_data(amount=message.text)
    await state.set_state(DealStates.waiting_description)
    await message.answer(LANG["ru"]["enter_description"])

# ===================== FSM — DESCRIPTION =====================
@dp.message(DealStates.waiting_description)
async def deal_description(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    desc = message.text

    deal_id = str(uuid.uuid4())
    link = f"https://t.me/OBMIN24_bot?start={deal_id}"

    await message.answer(
        f"🎉 Сделка создана!\n\n"
        f"💰 Сумма: {amount} TON\n"
        f"📜 Описание: {desc}\n"
        f"🔗 Ссылка: {link}"
    )

    await state.clear()

# ===================== Запуск =====================
async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
