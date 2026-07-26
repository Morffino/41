import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv

load_dotenv()

# ---------- Конфигурация из .env ----------
class Config:
    TICKET_CATEGORY_ID = int(os.getenv('TICKET_CATEGORY_ID', 0))
    SUPPORT_ROLE_ID = int(os.getenv('SUPPORT_ROLE_ID', 0))
    LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', 0))
    TOKEN = os.getenv('DISCORD_TOKEN')

if not all([Config.TOKEN, Config.TICKET_CATEGORY_ID, Config.SUPPORT_ROLE_ID, Config.LOG_CHANNEL_ID]):
    raise ValueError("Не все переменные окружения заданы! Проверьте .env файл.")

# ---------- Бот ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Список категорий (отображается на кнопках)
CATEGORIES = [
    ("Общие вопросы", "general"),
    ("Вопросы по серверу", "server"),
    ("Восстановление вещей", "restore"),
    ("Технические проблемы", "tech"),
    ("Жалоба на игрока", "player_report"),
    ("Жалоба на Администрацию", "admin_report")
]

# ---------- Модальное окно с формой ----------
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

    def __init__(self, category_name: str, channel: discord.TextChannel, user: discord.Member):
        super().__init__()
        self.category_name = category_name
        self.channel = channel
        self.user = user

    async def on_submit(self, interaction: discord.Interaction):
        # Проверка, что форму заполняет владелец тикета
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Это не ваш тикет.", ephemeral=True)
            return

        # Embed с данными
        embed = discord.Embed(title="Информация о тикете", color=discord.Color.blue())
        embed.add_field(name="Категория", value=self.category_name, inline=False)
        embed.add_field(name="SteamID64", value=self.steamid.value, inline=False)
        embed.add_field(name="Ник в игре", value=self.nickname.value, inline=False)
        embed.add_field(name="Кратко о проблеме", value=self.brief.value, inline=False)
        embed.set_footer(text=f"От: {interaction.user.display_name}")

        await self.channel.send(embed=embed)

        # Логирование в лог-канал
        log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="🆕 Новый тикет", color=discord.Color.gold())
            log_embed.add_field(name="Пользователь", value=interaction.user.mention, inline=False)
            log_embed.add_field(name="Канал", value=self.channel.mention, inline=False)
            log_embed.add_field(name="Категория", value=self.category_name, inline=False)
            log_embed.add_field(name="SteamID", value=self.steamid.value, inline=False)
            log_embed.add_field(name="Ник", value=self.nickname.value, inline=False)
            log_embed.add_field(name="Проблема", value=self.brief.value, inline=False)
            await log_channel.send(embed=log_embed)

        # Кнопка закрытия тикета
        close_btn = CloseTicketButton()
        view = discord.ui.View()
        view.add_item(close_btn)
        await self.channel.send("🔒 Для закрытия тикета нажмите кнопку ниже.", view=view)

        await interaction.response.send_message("✅ Тикет создан! Информация отправлена в канал.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        await interaction.response.send_message("❌ Произошла ошибка при отправке формы.", ephemeral=True)
        print(error)

# ---------- Кнопка категории ----------
class TicketCategoryButton(discord.ui.Button):
    def __init__(self, label: str, category_name: str):
        super().__init__(label=label, style=discord.ButtonStyle.primary, custom_id=f"ticket_{category_name}")
        self.category_name = category_name

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, id=Config.TICKET_CATEGORY_ID)
        if not category:
            await interaction.response.send_message("❌ Категория для тикетов не настроена.", ephemeral=True)
            return

        # Проверка на уже открытый тикет у пользователя
        existing = discord.utils.get(category.channels, topic=str(interaction.user.id))
        if existing:
            await interaction.response.send_message(f"⚠️ У вас уже есть открытый тикет: {existing.mention}", ephemeral=True)
            return

        # Настройка прав доступа к каналу
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.get_role(Config.SUPPORT_ROLE_ID): discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }
        channel_name = f"ticket-{interaction.user.name.lower()}"
        try:
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=str(interaction.user.id)   # сохраняем ID создателя
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка создания канала: {e}", ephemeral=True)
            return

        # Приветственное сообщение в новом канале
        embed = discord.Embed(
            title="📩 Тикет создан",
            description=f"Категория: **{self.category_name}**\nПожалуйста, заполните форму ниже.",
            color=discord.Color.green()
        )
        await channel.send(embed=embed)

        # Открываем модальное окно
        modal = TicketModal(category_name=self.category_name, channel=channel, user=interaction.user)
        await interaction.response.send_modal(modal)

# ---------- Кнопка закрытия тикета ----------
class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="close_ticket")

    async def callback(self, interaction: discord.Interaction):
        channel = interaction.channel
        if not channel.category or channel.category.id != Config.TICKET_CATEGORY_ID:
            await interaction.response.send_message("❌ Это не канал тикета.", ephemeral=True)
            return

        creator_id = channel.topic
        if creator_id is None:
            await interaction.response.send_message("❌ Не удалось определить создателя тикета.", ephemeral=True)
            return
        creator_id = int(creator_id)

        # Права: создатель или роль поддержки
        if interaction.user.id != creator_id and not interaction.user.get_role(Config.SUPPORT_ROLE_ID):
            await interaction.response.send_message("⛔ У вас нет прав на закрытие этого тикета.", ephemeral=True)
            return

        await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)

        # Логирование
        log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red())
            log_embed.add_field(name="Канал", value=channel.name, inline=False)
            log_embed.add_field(name="Закрыл", value=interaction.user.mention, inline=False)
            await log_channel.send(embed=log_embed)

        await channel.delete()

# ---------- Представление с кнопками категорий ----------
class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, _ in CATEGORIES:
            self.add_item(TicketCategoryButton(label=label, category_name=label))

# ---------- Команда /ticket_setup (только для админов) ----------
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

# ---------- Команда /close (альтернатива кнопке) ----------
@bot.tree.command(name="close", description="Закрыть текущий тикет")
async def close_ticket(interaction: discord.Interaction):
    channel = interaction.channel
    if not channel.category or channel.category.id != Config.TICKET_CATEGORY_ID:
        await interaction.response.send_message("❌ Это не канал тикета.", ephemeral=True)
        return

    creator_id = channel.topic
    if creator_id is None:
        await interaction.response.send_message("❌ Не удалось определить создателя тикета.", ephemeral=True)
        return
    creator_id = int(creator_id)

    if interaction.user.id != creator_id and not interaction.user.get_role(Config.SUPPORT_ROLE_ID):
        await interaction.response.send_message("⛔ У вас нет прав на закрытие этого тикета.", ephemeral=True)
        return

    await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)

    log_channel = interaction.guild.get_channel(Config.LOG_CHANNEL_ID)
    if log_channel:
        log_embed = discord.Embed(title="🔒 Тикет закрыт", color=discord.Color.red())
        log_embed.add_field(name="Канал", value=channel.name, inline=False)
        log_embed.add_field(name="Закрыл", value=interaction.user.mention, inline=False)
        await log_channel.send(embed=log_embed)

    await channel.delete()

# ---------- Событие готовности ----------
@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    try:
        synced = await bot.tree.sync()
        print(f"🔄 Синхронизировано {len(synced)} команд.")
    except Exception as e:
        print(e)

# ---------- Запуск ----------
if __name__ == "__main__":
    bot.run(Config.TOKEN)