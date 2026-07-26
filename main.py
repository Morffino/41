import discord
from discord import app_commands
from discord.ext import commands
import os
import sys
import asyncio
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

# ---------- Конфигурация ----------
TOKEN = os.getenv('DISCORD_TOKEN')
TICKET_CATEGORY_ID = os.getenv('TICKET_CATEGORY_ID')
SUPPORT_ROLE_ID = os.getenv('SUPPORT_ROLE_ID')
LOG_CHANNEL_ID = os.getenv('LOG_CHANNEL_ID')

missing = []
if not TOKEN: missing.append('DISCORD_TOKEN')
if not TICKET_CATEGORY_ID: missing.append('TICKET_CATEGORY_ID')
if not SUPPORT_ROLE_ID: missing.append('SUPPORT_ROLE_ID')
if not LOG_CHANNEL_ID: missing.append('LOG_CHANNEL_ID')

if missing:
    print("❌ Ошибка: не заданы переменные окружения:", ", ".join(missing))
    sys.exit(1)

try:
    TICKET_CATEGORY_ID = int(TICKET_CATEGORY_ID)
    SUPPORT_ROLE_ID = int(SUPPORT_ROLE_ID)
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
except ValueError:
    print("❌ Ошибка: ID должны быть числами.")
    sys.exit(1)

# ---------- Счётчик тикетов ----------
COUNTER_FILE = "data/ticket_counter.txt"
ticket_counter = 0
counter_lock = asyncio.Lock()

def load_counter():
    global ticket_counter
    if not os.path.exists(COUNTER_FILE):
        ticket_counter = 1
        return
    try:
        with open(COUNTER_FILE, "r") as f:
            ticket_counter = int(f.read().strip())
    except:
        ticket_counter = 1

def save_counter():
    os.makedirs(os.path.dirname(COUNTER_FILE), exist_ok=True)
    with open(COUNTER_FILE, "w") as f:
        f.write(str(ticket_counter))

# ---------- Бот ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

CATEGORIES = [
    ("Общие вопросы", "general"),
    ("Вопросы по серверу", "server"),
    ("Восстановление вещей", "restore"),
    ("Технические проблемы", "tech"),
    ("Жалоба на игрока", "player_report"),
    ("Жалоба на Администрацию", "admin_report")
]

# ---------- Модальное окно ----------
class TicketModal(discord.ui.Modal, title='Создание тикета'):
    steamid = discord.ui.TextInput(
        label='SteamID64',
        placeholder='Введите ваш SteamID64',
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
        guild = interaction.guild
        category = discord.utils.get(guild.categories, id=TICKET_CATEGORY_ID)
        if not category:
            await interaction.response.send_message("❌ Категория для тикетов не найдена.", ephemeral=True)
            return

        # Проверка на уже существующий тикет по пользователю
        existing = discord.utils.get(category.channels, topic=str(interaction.user.id))
        if existing:
            await interaction.response.send_message(f"⚠️ У вас уже есть открытый тикет: {existing.mention}", ephemeral=True)
            return

        # Получаем следующий номер тикета (с блокировкой)
        async with counter_lock:
            current_number = ticket_counter
            ticket_counter += 1
            save_counter()

        # Формируем имя канала: ticket-001, ticket-002, ... ticket-100000
        channel_name = f"ticket-{current_number:05d}"  # 5 цифр с ведущими нулями

        # Права доступа
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.get_role(SUPPORT_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=str(interaction.user.id)  # сохраняем создателя
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка создания канала: {e}", ephemeral=True)
            # Откатываем счётчик, чтобы номер не потерялся
            async with counter_lock:
                ticket_counter = current_number
                save_counter()
            return

        # Отправляем данные в канал
        embed = discord.Embed(title="Информация о тикете", color=discord.Color.blue())
        embed.add_field(name="Категория", value=self.category_name, inline=False)
        embed.add_field(name="SteamID64", value=self.steamid.value, inline=False)
        embed.add_field(name="Ник в игре", value=self.nickname.value, inline=False)
        embed.add_field(name="Кратко о проблеме", value=self.brief.value, inline=False)
        embed.set_footer(text=f"От: {interaction.user.display_name}")
        await channel.send(embed=embed)

        # Кнопка закрытия
        close_btn = CloseTicketButton()
        view = discord.ui.View()
        view.add_item(close_btn)
        await channel.send("🔒 Для закрытия тикета нажмите кнопку ниже.", view=view)

        # Логирование
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="🆕 Новый тикет", color=discord.Color.gold())
            log_embed.add_field(name="Номер", value=f"#{current_number:05d}", inline=False)
            log_embed.add_field(name="Пользователь", value=interaction.user.mention, inline=False)
            log_embed.add_field(name="Канал", value=channel.mention, inline=False)
            log_embed.add_field(name="Категория", value=self.category_name, inline=False)
            log_embed.add_field(name="SteamID", value=self.steamid.value, inline=False)
            log_embed.add_field(name="Ник", value=self.nickname.value, inline=False)
            log_embed.add_field(name="Проблема", value=self.brief.value, inline=False)
            await log_channel.send(embed=log_embed)

        await interaction.response.send_message(f"✅ Тикет создан! Перейдите в {channel.mention}", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message("❌ Произошла ошибка при отправке формы.", ephemeral=True)
        print(error)

# ---------- Кнопка категории ----------
class TicketCategoryButton(discord.ui.Button):
    def __init__(self, label: str, category_name: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=f"ticket_{category_name}")
        self.category_name = category_name

    async def callback(self, interaction: discord.Interaction):
        modal = TicketModal(category_name=self.category_name)
        await interaction.response.send_modal(modal)

# ---------- Кнопка закрытия ----------
class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="close_ticket")

    async def callback(self, interaction: discord.Interaction):
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

        await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red())
            log_embed.add_field(name="Канал", value=channel.name, inline=False)
            log_embed.add_field(name="Закрыл", value=interaction.user.mention, inline=False)
            await log_channel.send(embed=log_embed)

        await channel.delete()

# ---------- Представление с кнопками ----------
class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, _ in CATEGORIES:
            self.add_item(TicketCategoryButton(label=label, category_name=label))

# ---------- Команда /ticket_setup ----------
@bot.tree.command(name="ticket_setup", description="Создать сообщение с кнопками для открытия тикетов")
@app_commands.default_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="HS TICKET | Центр поддержки",
        description=(
            "Нужна помощь, восстановление. Выбери подходящую тему кнопки обращения.\n\n"
            "**Важно:** создавайте текст только увидит нужная команда."
        ),
        color=discord.Color.blue()
    )
    view = TicketSetupView()
    await interaction.response.send_message(embed=embed, view=view)

# ---------- Команда /close ----------
@bot.tree.command(name="close", description="Закрыть текущий тикет")
async def close_ticket(interaction: discord.Interaction):
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
    await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        log_embed = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red())
        log_embed.add_field(name="Канал", value=channel.name, inline=False)
        log_embed.add_field(name="Закрыл", value=interaction.user.mention, inline=False)
        await log_channel.send(embed=log_embed)
    await channel.delete()

# ---------- Веб-сервер для health check ----------
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

# ---------- Событие готовности ----------
@bot.event
async def on_ready():
    load_counter()
    print(f'✅ Бот {bot.user} запущен! Текущий счётчик тикетов: {ticket_counter}')
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Синхронизировано {len(synced)} команд.")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации: {e}")

# ---------- Запуск бота и веб-сервера параллельно ----------
async def main():
    asyncio.create_task(start_web_server())
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
