import discord
from discord import app_commands
from discord.ext import commands
import os
import sys
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

# ---------- Конфигурация ----------
TOKEN = os.getenv('DISCORD_TOKEN')
TICKET_CATEGORY_ID = int(os.getenv('TICKET_CATEGORY_ID', 0))
SUPPORT_ROLE_ID = int(os.getenv('SUPPORT_ROLE_ID', 0))
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', 0))

if not all([TOKEN, TICKET_CATEGORY_ID, SUPPORT_ROLE_ID, LOG_CHANNEL_ID]):
    print("❌ Ошибка: не заданы все переменные окружения.")
    sys.exit(1)

# ---------- Счётчик тикетов ----------
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
    os.makedirs(os.path.dirname(COUNTER_FILE), exist_ok=True)
    with open(COUNTER_FILE, "w") as f:
        f.write(str(ticket_counter))

# ---------- Логирование ----------
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_lock = asyncio.Lock()

def get_log_path(ticket_number: int) -> str:
    return os.path.join(LOG_DIR, f"ticket-{ticket_number:05d}.log")

async def write_ticket_log(ticket_number: int, text: str):
    async with log_lock:
        path = get_log_path(ticket_number)
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")

async def read_ticket_log(ticket_number: int) -> str:
    path = get_log_path(ticket_number)
    if not os.path.exists(path):
        return "Лог пуст."
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

async def delete_ticket_log(ticket_number: int):
    path = get_log_path(ticket_number)
    if os.path.exists(path):
        os.remove(path)

# ---------- Бот ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

bot.category = None
bot.support_role = None
bot.log_channel = None

# ---------- Категории с эмодзи и цветами ----------
# Формат: (название, идентификатор, эмодзи, цвет кнопки)
CATEGORIES = [
    ("Общие вопросы", "general", "❓", discord.ButtonStyle.primary),
    ("Восстановление вещей", "restore", "📦", discord.ButtonStyle.success),
    ("Технические проблемы", "tech", "🔧", discord.ButtonStyle.secondary),
    ("Жалоба на игрока/группировку", "player_report", "⚠️", discord.ButtonStyle.danger),
    ("Жалоба на Администрацию", "admin_report", "🚨", discord.ButtonStyle.danger)
]

# ---------- Модальное окно ----------
class TicketModal(discord.ui.Modal, title='📩 Создание тикета'):
    steamid = discord.ui.TextInput(
        label='SteamID64',
        placeholder='Введите ваш SteamID64 (только цифры)',
        required=True
    )
    nickname = discord.ui.TextInput(
        label='Ник в игре',
        placeholder='Укажите игровой ник',
        required=True
    )
    brief = discord.ui.TextInput(
        label='Кратко о проблеме',
        placeholder='До 30 символов',
        max_length=30,
        required=True
    )

    def __init__(self, category_name: str):
        super().__init__()
        self.category_name = category_name

    async def on_submit(self, interaction: discord.Interaction):
        global ticket_counter

        steam = self.steamid.value.strip()
        if not steam.isdigit():
            await interaction.response.send_message("❌ SteamID64 должен содержать только цифры.", ephemeral=True)
            return
        if len(steam) > 20:
            await interaction.response.send_message("❌ SteamID64 слишком длинный (максимум 20 цифр).", ephemeral=True)
            return

        guild = interaction.guild
        category = bot.category
        support_role = bot.support_role
        if not category or not support_role:
            await interaction.response.send_message("❌ Сервер не настроен правильно (категория или роль не найдены).", ephemeral=True)
            return

        existing = discord.utils.get(category.channels, topic=str(interaction.user.id))
        if existing:
            await interaction.response.send_message(f"⚠️ У вас уже есть открытый тикет: {existing.mention}", ephemeral=True)
            return

        async with counter_lock:
            current_number = ticket_counter
            ticket_counter += 1
            save_counter()

        channel_name = f"ticket-{current_number:05d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            support_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=str(interaction.user.id)
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка создания канала: {e}", ephemeral=True)
            async with counter_lock:
                ticket_counter = current_number
                save_counter()
            return

        await write_ticket_log(current_number, f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Тикет создан пользователем {interaction.user} (ID: {interaction.user.id})")
        await write_ticket_log(current_number, f"Категория: {self.category_name}")
        await write_ticket_log(current_number, f"SteamID64: {steam}")
        await write_ticket_log(current_number, f"Ник: {self.nickname.value}")
        await write_ticket_log(current_number, f"Проблема: {self.brief.value}")

        embed = discord.Embed(title="📋 Информация о тикете", color=discord.Color.blue())
        embed.add_field(name="Категория", value=self.category_name, inline=False)
        embed.add_field(name="SteamID64", value=steam, inline=False)
        embed.add_field(name="Ник в игре", value=self.nickname.value, inline=False)
        embed.add_field(name="Кратко о проблеме", value=self.brief.value, inline=False)
        embed.set_footer(text=f"От: {interaction.user.display_name}")
        await channel.send(embed=embed)

        view = discord.ui.View()
        view.add_item(CloseTicketButton())
        view.add_item(VerifyTicketButton())
        await channel.send("🔒 Для закрытия тикета нажмите кнопку ниже.", view=view)

        log_channel = bot.log_channel
        if log_channel:
            log_embed = discord.Embed(title="🆕 Новый тикет", color=discord.Color.gold())
            log_embed.add_field(name="Номер", value=f"#{current_number:05d}", inline=False)
            log_embed.add_field(name="Пользователь", value=interaction.user.mention, inline=False)
            log_embed.add_field(name="Канал", value=channel.mention, inline=False)
            log_embed.add_field(name="Категория", value=self.category_name, inline=False)
            await log_channel.send(embed=log_embed)

        await interaction.response.send_message(f"✅ Тикет создан! Перейдите в {channel.mention}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message("❌ Произошла ошибка при отправке формы.", ephemeral=True)
        print(f"Ошибка в модальном окне: {error}")

# ---------- Кнопка категории (с эмодзи и цветом) ----------
class TicketCategoryButton(discord.ui.Button):
    def __init__(self, label: str, category_name: str, emoji: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style, custom_id=f"ticket_{category_name}", emoji=emoji)
        self.category_name = category_name

    async def callback(self, interaction: discord.Interaction):
        modal = TicketModal(category_name=self.category_name)
        await interaction.response.send_modal(modal)

# ---------- Кнопка закрытия ----------
class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="close_ticket", emoji="🔒")

    async def callback(self, interaction: discord.Interaction):
        await self._close(interaction, verified=False)

    async def _close(self, interaction: discord.Interaction, verified: bool):
        channel = interaction.channel
        if not channel.category or channel.category.id != TICKET_CATEGORY_ID:
            await interaction.response.send_message("❌ Это не канал тикета.", ephemeral=True)
            return

        creator_id = channel.topic
        if creator_id is None:
            await interaction.response.send_message("❌ Не удалось определить создателя.", ephemeral=True)
            return
        creator_id = int(creator_id)

        if interaction.user.id != creator_id and not interaction.user.get_role(SUPPORT_ROLE_ID):
            await interaction.response.send_message("⛔ У вас нет прав на закрытие.", ephemeral=True)
            return

        try:
            ticket_number = int(channel.name.split('-')[1])
        except:
            ticket_number = None

        await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)

        if ticket_number:
            status = "ПРОВЕРЕН" if verified else "ЗАКРЫТ"
            admin = interaction.user.display_name if verified else ""
            log_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Тикет {status}"
            if verified:
                log_msg += f" администратором {interaction.user} (ник: {admin})"
            await write_ticket_log(ticket_number, log_msg)

            log_content = await read_ticket_log(ticket_number)
            log_channel = bot.log_channel
            if log_channel:
                if log_content.strip():
                    temp_path = f"/tmp/ticket_{ticket_number:05d}.log"
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(log_content)
                    await log_channel.send(
                        f"📄 Лог тикета #{ticket_number:05d} ({status})",
                        file=discord.File(temp_path, filename=f"ticket_{ticket_number:05d}.log")
                    )
                    os.remove(temp_path)
                else:
                    await log_channel.send(f"📄 Лог тикета #{ticket_number:05d} пуст.")
            await delete_ticket_log(ticket_number)

        await channel.delete()

# ---------- Кнопка "Тикет проверен" ----------
class VerifyTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Тикет проверен", style=discord.ButtonStyle.success, custom_id="verify_ticket", emoji="✅")

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.get_role(SUPPORT_ROLE_ID):
            await interaction.response.send_message("⛔ Эта кнопка доступна только для поддержки.", ephemeral=True)
            return
        close_btn = CloseTicketButton()
        await close_btn._close(interaction, verified=True)

# ---------- Представление с кнопками ----------
class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, ident, emoji, style in CATEGORIES:
            self.add_item(TicketCategoryButton(label=label, category_name=label, emoji=emoji, style=style))

ticket_setup_view = TicketSetupView()

# ---------- Команда /ticket_setup ----------
@bot.tree.command(name="ticket_setup", description="Создать сообщение с кнопками для открытия тикетов")
@app_commands.default_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 HS TICKET | Центр поддержки",
        description=(
            "Нужна помощь, восстановление. Выбери подходящую тему кнопки обращения.\n\n"
            "**Важно:** создавайте текст только увидит нужная команда."
        ),
        color=discord.Color.blue()
    )
    await interaction.response.send_message(embed=embed, view=ticket_setup_view)

# ---------- Команда /close ----------
@bot.tree.command(name="close", description="Закрыть текущий тикет")
async def close_ticket(interaction: discord.Interaction):
    close_btn = CloseTicketButton()
    await close_btn._close(interaction, verified=False)

# ---------- Обработчик сообщений для логирования ----------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not message.channel.category or message.channel.category.id != TICKET_CATEGORY_ID:
        await bot.process_commands(message)
        return
    channel_name = message.channel.name
    if not channel_name.startswith("ticket-"):
        await bot.process_commands(message)
        return
    try:
        ticket_number = int(channel_name.split('-')[1])
    except:
        await bot.process_commands(message)
        return
    log_line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message.author} (ID: {message.author.id}): {message.content}"
    await write_ticket_log(ticket_number, log_line)
    await bot.process_commands(message)

# ---------- Веб-сервер ----------
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=8080)
    await site.start()
    print("🌐 Веб-сервер для health check запущен на порту 8080")
    await asyncio.Event().wait()

@bot.event
async def on_ready():
    load_counter()
    print(f'✅ Бот {bot.user} запущен! Текущий счётчик: {ticket_counter}')
    guild = bot.guilds[0]
    if not guild:
        print("⚠️ Бот не состоит ни на одном сервере.")
        return
    bot.category = guild.get_channel(TICKET_CATEGORY_ID)
    bot.support_role = guild.get_role(SUPPORT_ROLE_ID)
    bot.log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not bot.category:
        print(f"⚠️ Категория с ID {TICKET_CATEGORY_ID} не найдена.")
    if not bot.support_role:
        print(f"⚠️ Роль с ID {SUPPORT_ROLE_ID} не найдена.")
    if not bot.log_channel:
        print(f"⚠️ Лог-канал с ID {LOG_CHANNEL_ID} не найден.")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Синхронизировано {len(synced)} команд.")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации: {e}")

async def main():
    asyncio.create_task(start_web_server())
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
