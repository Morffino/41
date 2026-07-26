import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
from aiohttp import web
import asyncio

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ Токен не задан")
    exit(1)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} готов!')
    try:
        synced = await bot.tree.sync()
        print(f'🔁 Синхронизировано {len(synced)} команд')
    except Exception as e:
        print(f'⚠️ Ошибка синхронизации: {e}')

@bot.tree.command(name="ping", description="Проверка работы")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong!", ephemeral=True)

# Веб-сервер для health check (обязательно)
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=8080)
    await site.start()
    print("🌐 Health check на 8080")
    await asyncio.Event().wait()

async def main():
    asyncio.create_task(start_web())
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
from aiohttp import web
import asyncio

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

if not TOKEN:
    print("❌ Токен не задан")
    exit(1)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} готов!')
    try:
        synced = await bot.tree.sync()
        print(f'🔁 Синхронизировано {len(synced)} команд')
    except Exception as e:
        print(f'⚠️ Ошибка синхронизации: {e}')

@bot.tree.command(name="ping", description="Проверка работы")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong!", ephemeral=True)

# Веб-сервер для health check (обязательно)
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=8080)
    await site.start()
    print("🌐 Health check на 8080")
    await asyncio.Event().wait()

async def main():
    asyncio.create_task(start_web())
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
