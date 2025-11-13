import asyncio
import uuid
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from dotenv import load_dotenv
import os

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(storage=MemoryStorage())

# ===================== Локализация =====================
LANG = {
    "ru": {
        "main_menu": "Главное меню",
        "new_deal": "Новая сделка",
        "add_wallet": "Добавить/Изменить кошелек",
        "ton_wallet": "TON-Кошелек",
        "card": "Карта (UA)",
        "stars": "Звезды",
        "referral": "Реферальная ссылка",
        "change_lang": "Сменить язык",
        "support": "Поддержка",
        "enter_amount": "Введите сумму. Например: 100.5",
        "enter_description": "📝 Опишите, что вы предлагаете:\nПример: 10 кепок и пепочка",
        "deal_created": "🎉 Сделка создана!",
        "wallet_missing": "Вы не указали кошелек!",
    },
    "uk": {
        "main_menu": "Головне меню",
        "new_deal": "Нова угода",
        "add_wallet": "Додати/Змінити гаманець",
        "ton_wallet": "TON-Гаманець",
        "card": "Карта (UA)",
        "stars": "Зірки",
        "referral": "Реферальний лінк",
        "change_lang": "Змінити мову",
        "support": "Підтримка",
        "enter_amount": "Введіть суму. Наприклад: 100.5",
        "enter_description": "📝 Опишіть, що пропонуєте:\nПриклад: 10 кепок і пепочка",
        "deal_created": "🎉 Угоду створено!",
        "wallet_missing": "Ви не вказали гаманець!",
    },
    "en": {
        "main_menu": "Main Menu",
        "new_deal": "New Deal",
        "add_wallet": "Add/Change Wallet",
        "ton_wallet": "TON Wallet",
        "card": "Card (UA)",
        "stars": "Stars",
        "referral": "Referral Link",
        "change_lang": "Change Language",
        "support": "Support",
        "enter_amount": "Enter amount. Example: 100.5",
        "enter_description": "📝 Describe your offer:\nExample: 10 caps and pepochka",
        "deal_created": "🎉 Deal created!",
        "wallet_missing": "You did not specify a wallet!",
    }
}

# ===================== FSM =====================
class DealStates(StatesGroup):
    waiting_amount = State()
    waiting_description = State()

# ===================== Меню =====================
def main_menu(lang="ru"):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton(LANG[lang]["new_deal"]))
    kb.add(KeyboardButton(LANG[lang]["add_wallet"]))
    kb.add(KeyboardButton(LANG[lang]["referral"]))
    kb.add(KeyboardButton(LANG[lang]["change_lang"]))
    kb.add(KeyboardButton(LANG[lang]["support"]))
    return kb

# ===================== Хендлеры =====================
user_lang = {}  # хранение языка пользователя
user_wallet = {}  # хранение TON-кошелька

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user_lang[message.from_user.id] = "ru"
    await message.answer(f"👋 Привет, {message.from_user.full_name}!", reply_markup=main_menu("ru"))

@dp.message(lambda m: m.text == LANG[user_lang.get(m.from_user.id, 'ru')]["new_deal"])
async def new_deal(message: types.Message, state: FSMContext):
    lang = user_lang.get(message.from_user.id, 'ru')
    if message.from_user.id not in user_wallet:
        await message.answer(LANG[lang]["wallet_missing"])
        return
    await message.answer(LANG[lang]["enter_amount"])
    await state.set_state(DealStates.waiting_amount)

@dp.message(DealStates.waiting_amount)
async def deal_amount(message: types.Message, state: FSMContext):
    await state.update_data(amount=message.text)
    lang = user_lang.get(message.from_user.id, 'ru')
    await message.answer(LANG[lang]["enter_description"])
    await state.set_state(DealStates.waiting_description)

@dp.message(DealStates.waiting_description)
async def deal_description(message: types.Message, state: FSMContext):
    data = await state.get_data()
    amount = data.get("amount")
    description = message.text
    lang = user_lang.get(message.from_user.id, 'ru')
    deal_id = str(uuid.uuid4())
    # Генерация ссылки (uuid)
    link = f"https://t.me/OBMIN24_bot?start={deal_id}"
    await message.answer(
        f"{LANG[lang]['deal_created']}\n\n"
        f"💰 Сумма: {amount} TON\n"
        f"📜 Описание: {description}\n"
        f"🔗 Ссылка для покупателя: {link}"
    )
    await state.clear()

@dp.message(lambda m: m.text == LANG[user_lang.get(m.from_user.id, 'ru')]["add_wallet"])
async def add_wallet(message: types.Message):
    await message.answer("Введите ваш TON-кошелек:")
    # Дальше можно хранить в user_wallet

@dp.message(lambda m: m.text == LANG[user_lang.get(m.from_user.id, 'ru')]["referral"])
async def referral(message: types.Message):
    lang = user_lang.get(message.from_user.id, 'ru')
    ref_link = f"https://t.me/OBMIN24_bot?start={message.from_user.id}"
    await message.answer(f"{LANG[lang]['referral']}\n{ref_link}")

@dp.message(lambda m: m.text == LANG[user_lang.get(m.from_user.id, 'ru')]["change_lang"])
async def change_lang(message: types.Message):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("🇷🇺 Русский"))
    kb.add(KeyboardButton("🇺🇦 Українська"))
    kb.add(KeyboardButton("🇬🇧 English"))
    await message.answer("Выберите язык / Оберіть мову / Choose language:", reply_markup=kb)

@dp.message(lambda m: m.text in ["🇷🇺 Русский","🇺🇦 Українська","🇬🇧 English"])
async def set_lang(message: types.Message):
    if message.text == "🇷🇺 Русский":
        user_lang[message.from_user.id] = "ru"
    elif message.text == "🇺🇦 Українська":
        user_lang[message.from_user.id] = "uk"
    else:
        user_lang[message.from_user.id] = "en"
    lang = user_lang[message.from_user.id]
    await message.answer("✅ Язык изменен!", reply_markup=main_menu(lang))

@dp.message(lambda m: m.text == LANG[user_lang.get(m.from_user.id, 'ru')]["support"])
async def support(message: types.Message):
    await message.answer("🆘 Поддержка: @obmin24supporter_bot")

# ===================== Запуск =====================
async def main():
    print("BOT STARTED")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
