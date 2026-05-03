import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# --- НАСТРОЙКИ ---
API_TOKEN = 'ТВОЙ_ТОКЕН_ТУТ'
logging.basicConfig(level=logging.INFO)

# --- БАЗА ДАННЫХ (Упрощенная для телефона) ---
def init_db():
    conn = sqlite3.connect('looksmax.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                   (user_id INTEGER PRIMARY KEY, username TEXT, level INTEGER DEFAULT 1, xp INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS progress_photos 
                   (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, file_id TEXT, date TEXT)''')
    conn.commit()
    conn.close()

def add_user(user_id, username):
    conn = sqlite3.connect('looksmax.db')
    cursor = conn.cursor()
    cursor.execute('INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)', (user_id, username))
    conn.commit()
    conn.close()

def add_xp(user_id, amount):
    conn = sqlite3.connect('looksmax.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET xp = xp + ? WHERE user_id = ?', (amount, user_id))
    conn.commit()
    conn.close()

# --- ЛОГИКА БОТА ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Кнопки меню
def main_menu():
    builder = ReplyKeyboardBuilder()
    builder.button(text="👤 Профиль")
    builder.button(text="📸 Отправить фото")
    builder.button(text="📚 Советы")
    builder.adjust(2) # Кнопки по 2 в ряд
    return builder.as_markup(resize_keyboard=True)

@dp.message(Command("start"))
async def start_handler(message: types.Message):
    add_user(message.from_user.id, message.from_user.username)
    await message.answer(f"Привет, {message.from_user.first_name}! Это твой бот для Looksmaxing. Прокачивай себя каждый день.", 
                         reply_markup=main_menu())

@dp.message(F.text == "👤 Профиль")
async def profile_handler(message: types.Message):
    conn = sqlite3.connect('looksmax.db')
    cursor = conn.cursor()
    user = cursor.execute('SELECT level, xp FROM users WHERE user_id = ?', (message.from_user.id,)).fetchone()
    conn.close()
    
    if user:
        await message.answer(f"Твой уровень: {user[0]} 🏆\nТвой опыт: {user[1]} XP")

@dp.message(F.photo)
async def photo_handler(message: types.Message):
    file_id = message.photo[-1].file_id
    conn = sqlite3.connect('looksmax.db')
    cursor = conn.cursor()
    cursor.execute('INSERT INTO progress_photos (user_id, file_id, date) VALUES (?, ?, ?)', 
                   (message.from_user.id, file_id, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    
    add_xp(message.from_user.id, 20)
    await message.answer("✅ Фото сохранено! +20 XP. Продолжай в том же духе!")

# --- ЗАПУСК ---
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
