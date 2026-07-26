import discord
from discord import app_commands
from discord.ext import commands
import os
import sys
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from aiohttp import web

# Импорт модулей
from logger import log_open, log_message, log_close, get_log_content, delete_log
from notifications import send_close_notification

load_dotenv()

# ---------- Проверка папок ----------
os.makedirs("data", exist_ok=True)
os.makedirs("logs", exist_ok=True)

# ---------- Конфигурация ----------
TOKEN = os.getenv('DISCORD_TOKEN')
TICKET_CATEGORY_ID = int(os.getenv('TICKET_CATEGORY_ID', 0))
SUPPORT_ROLE_ID = int(os.getenv('SUPPORT_ROLE_ID', 0))
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', 0))

if not all([TOKEN, TICKET_CATEGORY_ID, SUPPORT_ROLE_ID, LOG_CHANNEL_ID]):
    print("❌ Ошибка: не заданы все переменные окружения.")
    sys.exit(1)

# ---------- Счётчик ----------
COUNTER_FILE = "data/ticket_counter.txt"
ticket_counter = 1
counter_lock = asyncio.Lock()

def load_counter():
    global ticket_counter
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "r") as f:
            ticket_counter = int(f.read().strip())
    else:
        ticket_counter = 1

def save_counter():
    with open(COUNTER_FILE, "w") as f:
        f.write(str(ticket_counter))

# ---------- Бот ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

bot.category = None
bot.support_role = None
bot.log_channel = None
bot.ticket_open_time = {}

# ---------- Модальное окно (без изменений, см. выше) ----------
# ... (весь код модального окна, кнопок и команд остаётся тем же, что вы прислали)
# Я не буду повторять его полностью, чтобы не загромождать ответ.
# Но вы можете взять свой код main.py – он абсолютно рабочий.
