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

# ---------- Бот ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

bot.category = None
bot.support_role = None
bot.log_channel = None

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

        steam = self.steamid.value.strip()
        if not steam.isdigit():
            await interaction.response.send_message("❌ SteamID должен содержать только цифры.", ephemeral=True)
            return

        guild = interaction.guild
        category = bot.category
        support_role = bot.support_role
        if not category or not support_role:
            await interaction.response.send_message("❌ Категория или роль не найдены.", ephemeral=True)
            return

        existing = discord.utils.get(category.channels, topic=str(interaction.user.id))
        if existing:
            await interaction.response.send_message(f"⚠️ У вас уже есть тикет: {existing.mention}", ephemeral=True)
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
            await interaction.response.send_message(f"❌ Ошибка: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="Информация о тикете", color=discord.Color.blue())
        embed.add_field(name="Категория", value=self.category_name, inline=False)
        embed.add_field(name="SteamID64", value=steam, inline=False)
        embed.add_field(name="Ник", value=self.nickname.value, inline=False)
        embed.add_field(name="Проблема", value=self.brief.value, inline=False)
        embed.set_footer(text=f"От: {interaction.user.display_name}")
        await channel.send(embed=embed)

        close_btn = CloseTicketButton()
        view = discord.ui.View()
        view.add_item(close_btn)
        await channel.send("🔒 Для закрытия нажмите кнопку.", view=view)

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
        await interaction.response.send_message("❌ Ошибка.", ephemeral=True)
        print(error)

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
            await interaction.response.send_message("⛔ У вас нет прав.", ephemeral=True)
            return

        await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)

        log_channel = bot.log_channel
        if log_channel:
            log_embed = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red())
            log_embed.add_field(name="Канал", value=channel.name, inline=False)
            log_embed.add_field(name="Закрыл", value=interaction.user.mention, inline=False)
            await log_channel.send(embed=log_embed)

        await channel.delete()

# ---------- Кнопки категорий (с изменёнными названиями) ----------
class TicketCategoryButton(discord.ui.Button):
    def __init__(self, label: str, category_name: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=f"ticket_{category_name}")
        self.category_name = category_name

    async def callback(self, interaction: discord.Interaction):
        modal = TicketModal(category_name=self.category_name)
        await interaction.response.send_modal(modal)

# ---------- Представление с кнопками (обновлённый список) ----------
class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        # Новые названия с эмодзи (как в ваших последних правках)
        categories = [
            ("❔ Общие вопросы", "Общие вопросы"),
            ("📦 Восстановление имущества", "Восстановление имущества"),
            ("🛠️ Технические проблемы", "Технические проблемы"),
            ("⚠️ Жалоба на игрока / группировку", "Жалоба на игрока / группировку"),
            ("🛡️ Жалоба на Администрацию", "Жалоба на Администрацию")
        ]
        for label, cat_name in categories:
            self.add_item(TicketCategoryButton(label=label, category_name=cat_name))

# ---------- Команда /ticket_setup ----------
@bot.tree.command(name="ticket_setup", description="Создать сообщение с кнопками")
@app_commands.default_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="HS TICKET | Центр поддержки",
        description="Нужна помощь? Выберите тему кнопкой ниже.",
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
        await interaction.response.send_message("⛔ У вас нет прав.", ephemeral=True)
        return
    await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)
    log_channel = bot.log_channel
    if log_channel:
        log_embed = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red())
        log_embed.add_field(name="Канал", value=channel.name, inline=False)
        log_embed.add_field(name="Закрыл", value=interaction.user.mention, inline=False)
        await log_channel.send(embed=log_embed)
    await channel.delete()

# ---------- Веб-сервер (health check) ----------
async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web():
    app = web.Application()
    app.router.add_get('/health', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=8080)
    await site.start()
    print("🌐 Health check на порту 8080")
    await asyncio.Event().wait()

# ---------- Событие готовности ----------
@bot.event
async def on_ready():
    load_counter()
    print(f'✅ Бот {bot.user} запущен! Счётчик: {ticket_counter}')
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        print("⚠️ Бот не на сервере.")
        return
    bot.category = guild.get_channel(TICKET_CATEGORY_ID)
    bot.support_role = guild.get_role(SUPPORT_ROLE_ID)
    bot.log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not bot.category: print(f"⚠️ Категория {TICKET_CATEGORY_ID} не найдена.")
    if not bot.support_role: print(f"⚠️ Роль {SUPPORT_ROLE_ID} не найдена.")
    if not bot.log_channel: print(f"⚠️ Лог-канал {LOG_CHANNEL_ID} не найден.")
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Синхронизировано {len(synced)} команд.")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации: {e}")

async def main():
    asyncio.create_task(start_web())
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
