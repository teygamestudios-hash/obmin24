import asyncio
import uuid
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from aiogram.client.bot import DefaultBotProperties

# ===================== ENV =====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env файле")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

# ===================== Локализация =====================
LANG = {
    "ru": {
        "main_menu": "Главное меню",
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
    },
    "uk": {
        "main_menu": "Головне меню",
        "new_deal": "Нова угода",
        "add_wallet": "Додати/Змінити гаманець",
        "referral": "Реферальний лінк",
        "change_lang": "Змінити мову",
        "support": "Підтримка",
        "enter_amount": "Введіть суму. Наприклад: 100.5",
        "enter_description": "📝 Опишіть, що пропонуєте:\nПриклад: 10 кепок і пепочка",
        "deal_created": "🎉 Угоду створено!",
        "wallet_missing": "Ви не вказали гаманець!",
        "lang_changed": "✅ Мову змінено!"
    },
    "en": {
        "main_menu": "Main Menu",
        "new_deal": "New Deal",
        "add_wallet": "Add/Change Wallet",
        "referral": "Referral Link",
        "change_lang": "Change Language",
        "support": "Support",
        "enter_amount": "Enter amount. Example: 100.5",
        "enter_description": "📝 Describe your offer:\nExample: 10 caps and pepochka",
        "deal_created": "🎉 Deal created!",
        "wallet_missing": "You did not specify a wallet!",
        "lang_changed": "✅ Language changed!"
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


# ===================== Хендлеры =====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_lang[message.from_user.id] = "ru"
    await message.answer(f"👋 Привет, {message.from_user.full_name}!", reply_markup=main_menu("ru"))


# Универсальный роутер всех кнопок
@dp.message(F.text)
async def menu_router(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    lang = user_lang.get(uid, "ru")
    text = message.text

    # ===== Новая сделка =====
    if text == LANG[lang]["new_deal"]:
        if uid not in user_wallet:
            await message.answer(LANG[lang]["wallet_missing"])
            return
        await message.answer(LANG[lang]["enter_amount"])
        await state.set_state(DealStates.waiting_amount)
        return

    # ===== Добавить кошелёк =====
    if text == LANG[lang]["add_wallet"]:
        await message.answer("Введите ваш TON-кошелек:")
        await state.set_state(DealStates.waiting_wallet)
        return

    # ===== Рефералка =====
    if text == LANG[lang]["referral"]:
        ref = f"https://t.me/OBMIN24_bot?start={uid}"
        await message.answer(ref)
        return

    # ===== Изменить язык =====
    if text == LANG[lang]["change_lang"]:
        await message.answer("Выберите язык:", reply_markup=lang_menu())
        return

    # ===== Выбор языка =====
    if text in ["🇷🇺 Русский", "🇺🇦 Українська", "🇬🇧 English"]:
        if text == "🇷🇺 Русский":
            user_lang[uid] = "ru"
        elif text == "🇺🇦 Українська":
            user_lang[uid] = "uk"
        else:
            user_lang[uid] = "en"

        lang = user_lang[uid]
        await message.answer(LANG[lang]["lang_changed"], reply_markup=main_menu(lang))
        return

    # ===== Поддержка =====
    if text == LANG[lang]["support"]:
        await message.answer("🆘 Поддержка: @obmin24supporter_bot")
        return


# ===================== FSM Хендлеры =====================

@dp.message(DealStates.waiting_wallet)
async def save_wallet(message: types.Message, state: FSMContext):
    user_wallet[message.from_user.id] = message.text
    lang = user_lang.get(message.from_user.id, "ru")
    await message.answer("Готово ✓", reply_markup=main_menu(lang))
    await state.clear()


@dp.message(DealStates.waiting_amount)
async def deal_amount(message: types.Message, state: FSMContext):
    await state.update_data(amount=message.text)
    lang = user_lang.get(message.from_user.id, "ru")
    await message.answer(LANG[lang]["enter_description"])
    await state.set_state(DealStates.waiting_description)


@dp.message(DealStates.waiting_description)
async def deal_description(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data["amount"]
    description = message.text
    lang = user_lang.get(message.from_user.id, "ru")

    deal_id = str(uuid.uuid4())
    link = f"https://t.me/OBMIN24_bot?start={deal_id}"

    await message.answer(
        f"{LANG[lang]['deal_created']}\n\n"
        f"💰 Сумма: {amount} TON\n"
        f"📜 Описание: {description}\n"
        f"🔗 Ссылка: {link}"
    )

    await state.clear()


# ===================== Запуск =====================
async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
