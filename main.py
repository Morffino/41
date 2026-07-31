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
OWNER_ID = int(os.getenv('OWNER_ID', 0))                     # <-- Владелец бота
TICKET_CATEGORY_ID = int(os.getenv('TICKET_CATEGORY_ID', 0))
LOG_CHANNEL_ID = int(os.getenv('LOG_CHANNEL_ID', 0))

# Основная роль поддержки (для совместимости, не обязательна)
SUPPORT_ROLE_ID = int(os.getenv('SUPPORT_ROLE_ID', 0))

# Дополнительные роли поддержки (указанные вами)
ADDITIONAL_SUPPORT_ROLE_IDS = [
    1529252048883810485,
    1529253808666841302,
    1529253952850366616,
    1529254103820275823
]

# Все роли, имеющие доступ к тикетам
ALL_SUPPORT_ROLE_IDS = [SUPPORT_ROLE_ID] + ADDITIONAL_SUPPORT_ROLE_IDS if SUPPORT_ROLE_ID else ADDITIONAL_SUPPORT_ROLE_IDS

if not all([TOKEN, TICKET_CATEGORY_ID, LOG_CHANNEL_ID]):
    print("❌ Ошибка: не заданы все переменные окружения.")
    print("Необходимы: DISCORD_TOKEN, TICKET_CATEGORY_ID, LOG_CHANNEL_ID")
    sys.exit(1)

if OWNER_ID == 0:
    print("⚠️ OWNER_ID не задан – команды /ticket_setup и /close будут недоступны")

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
    os.makedirs(os.path.dirname(COUNTER_FILE), exist_ok=True)
    with open(COUNTER_FILE, "w") as f:
        f.write(str(ticket_counter))

# ---------- Логирование ----------
LOG_DIR = "logs"
if os.path.exists(LOG_DIR) and not os.path.isdir(LOG_DIR):
    os.remove(LOG_DIR)
os.makedirs(LOG_DIR, exist_ok=True)

log_lock = asyncio.Lock()

def get_log_path(ticket_number: int) -> str:
    return os.path.join(LOG_DIR, f"ticket-{ticket_number:05d}.log")

async def write_ticket_log(ticket_number: int, text: str):
    async with log_lock:
        path = get_log_path(ticket_number)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")

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
bot.log_channel = None
bot.ticket_open_time = {}

# ---------- Модальное окно для причины закрытия ----------
class CloseReasonModal(discord.ui.Modal, title='Укажите причину закрытия'):
    reason = discord.ui.TextInput(
        label='Причина закрытия',
        placeholder='Опишите причину...',
        required=True,
        max_length=200
    )

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason.value
        close_btn = CloseTicketButton()
        await close_btn._close(interaction, verified=False, reason=reason)

# ---------- Модальное окно создания тикета ----------
class TicketModal(discord.ui.Modal, title='Создание тикета'):
    steamid = discord.ui.TextInput(
        label='SteamID64',
        placeholder='Введите ваш SteamID64 (только цифры, 17-20 символов)',
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
        if len(steam) < 17 or len(steam) > 20:
            await interaction.response.send_message("❌ SteamID64 должен быть длиной от 17 до 20 цифр.", ephemeral=True)
            return

        guild = interaction.guild
        category = bot.category
        if not category:
            await interaction.response.send_message("❌ Категория не найдена.", ephemeral=True)
            return

        support_roles = []
        for role_id in ALL_SUPPORT_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                support_roles.append(role)

        if not support_roles:
            await interaction.response.send_message("❌ Ни одна из ролей поддержки не найдена.", ephemeral=True)
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
        }
        for role in support_roles:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

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

        bot.ticket_open_time[current_number] = datetime.now()

        await write_ticket_log(current_number, f"🟢 ТИКЕТ ОТКРЫТ")
        await write_ticket_log(current_number, f"   Пользователь: {interaction.user}")
        await write_ticket_log(current_number, f"   Категория: {self.category_name}")
        await write_ticket_log(current_number, f"   SteamID64: {steam}")
        await write_ticket_log(current_number, f"   Ник: {self.nickname.value}")
        await write_ticket_log(current_number, f"   Проблема: {self.brief.value}")

        embed = discord.Embed(title="Информация о тикете", color=discord.Color.blue())
        embed.add_field(name="Категория", value=self.category_name, inline=False)
        embed.add_field(name="SteamID64", value=steam, inline=False)
        embed.add_field(name="Ник", value=self.nickname.value, inline=False)
        embed.add_field(name="Проблема", value=self.brief.value, inline=False)
        embed.set_footer(text=f"От: {interaction.user.display_name}")
        await channel.send(embed=embed)

        welcome_embed = discord.Embed(
            title=f"🎫 Тикет #{current_number:05d} создан",
            description=(
                "**Теперь опишите подробнее ситуацию**\n"
                "Прикрепите доказательства, если они нужны (скриншоты, видео, логи).\n\n"
                "Общайтесь в этом канале, администраторы скоро ответят."
            ),
            color=discord.Color.green()
        )
        welcome_embed.set_footer(text="Команда поддержки ECLIPSE RP")
        await channel.send(embed=welcome_embed)

        view = discord.ui.View()
        view.add_item(CloseTicketButton())
        view.add_item(CloseWithReasonButton())
        await channel.send("🔒 Кнопки управления:", view=view)

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

# ---------- Кнопка закрытия (простая) ----------
class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="close_ticket")

    async def callback(self, interaction: discord.Interaction):
        await self._close(interaction, verified=False)

    async def _close(self, interaction: discord.Interaction, verified: bool, reason: str = None):
        channel = interaction.channel
        if not channel.category or channel.category.id != TICKET_CATEGORY_ID:
            await interaction.response.send_message("❌ Это не канал тикета.", ephemeral=True)
            return

        creator_id = channel.topic
        if creator_id is None:
            await interaction.response.send_message("❌ Не удалось определить создателя.", ephemeral=True)
            return
        creator_id = int(creator_id)

        has_support_role = False
        for role_id in ALL_SUPPORT_ROLE_IDS:
            if interaction.user.get_role(role_id):
                has_support_role = True
                break

        if interaction.user.id != creator_id and not has_support_role:
            await interaction.response.send_message("⛔ У вас нет прав.", ephemeral=True)
            return

        try:
            ticket_number = int(channel.name.split('-')[1])
        except:
            ticket_number = None

        await interaction.response.send_message("⏳ Тикет закрывается...", ephemeral=True)

        if ticket_number:
            status = "ПРОВЕРЕН" if verified else "ЗАКРЫТ"

            await write_ticket_log(ticket_number, f"🔴 ТИКЕТ {status}")
            await write_ticket_log(ticket_number, f"   Закрыл: {interaction.user}")
            if reason:
                await write_ticket_log(ticket_number, f"   Причина: {reason}")

            creator = await interaction.guild.fetch_member(creator_id)
            open_time = bot.ticket_open_time.get(ticket_number, datetime.now())
            close_time = datetime.now()

            if creator:
                embed = discord.Embed(
                    title=f"# Тикет #{ticket_number:05d} закрыт",
                    color=discord.Color.green() if verified else discord.Color.orange()
                )
                embed.add_field(name="Открыл тикет", value=creator.mention, inline=False)
                embed.add_field(name="Закрыл тикет", value=interaction.user.mention, inline=False)
                embed.add_field(name="Тикет открыт", value=open_time.strftime("%d %B %Y г. %H:%M"), inline=False)
                embed.add_field(name="Тикет закрыт", value=close_time.strftime("%d %B %Y г. %H:%M"), inline=False)
                embed.add_field(name="Причина закрытия", value=reason if reason else "Не указана", inline=False)
                embed.set_footer(text=close_time.strftime("%d.%m.%Y %H:%M"))

                log_content = await read_ticket_log(ticket_number)
                if log_content.strip():
                    temp_path = f"/tmp/ticket_{ticket_number:05d}.log"
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(log_content)
                    try:
                        await creator.send(embed=embed)
                        await creator.send(file=discord.File(temp_path, filename=f"ticket_{ticket_number:05d}.log"))
                    except Exception as e:
                        print(f"⚠️ Не удалось отправить ЛС: {e}")
                    os.remove(temp_path)
                else:
                    try:
                        await creator.send(embed=embed)
                    except Exception as e:
                        print(f"⚠️ Не удалось отправить ЛС: {e}")

            log_channel = bot.log_channel
            if log_channel:
                log_content = await read_ticket_log(ticket_number)
                if log_content.strip():
                    temp_path = f"/tmp/ticket_{ticket_number:05d}.log"
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(log_content)
                    try:
                        await log_channel.send(
                            f"📄 Лог тикета #{ticket_number:05d} ({status})",
                            file=discord.File(temp_path, filename=f"ticket_{ticket_number:05d}.log")
                        )
                    except:
                        pass
                    os.remove(temp_path)
                else:
                    await log_channel.send(f"📄 Лог тикета #{ticket_number:05d} пуст.")
            await delete_ticket_log(ticket_number)

            if ticket_number in bot.ticket_open_time:
                del bot.ticket_open_time[ticket_number]

        await channel.delete()

# ---------- Кнопка закрытия с причиной ----------
class CloseWithReasonButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Закрыть с причиной", style=discord.ButtonStyle.secondary, custom_id="close_with_reason")

    async def callback(self, interaction: discord.Interaction):
        has_support_role = False
        for role_id in ALL_SUPPORT_ROLE_IDS:
            if interaction.user.get_role(role_id):
                has_support_role = True
                break
        if not has_support_role:
            await interaction.response.send_message("⛔ Только для поддержки.", ephemeral=True)
            return
        modal = CloseReasonModal()
        await interaction.response.send_modal(modal)

# ---------- Кнопки категорий ----------
class TicketCategoryButton(discord.ui.Button):
    def __init__(self, label: str, category_name: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style, custom_id=f"ticket_{category_name}")
        self.category_name = category_name

    async def callback(self, interaction: discord.Interaction):
        modal = TicketModal(category_name=self.category_name)
        await interaction.response.send_modal(modal)

# ---------- Представление ----------
class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        categories = [
            ("❔ Общие вопросы", "Общие вопросы", discord.ButtonStyle.primary),
            ("📦 Восстановление имущества", "Восстановление имущества", discord.ButtonStyle.success),
            ("🛠️ Технические проблемы", "Технические проблемы", discord.ButtonStyle.secondary),
            ("⚠️ Жалоба на игрока/группировку", "Жалоба на игрока/группировку", discord.ButtonStyle.danger),
            ("🛡️ Жалоба на администрацию", "Жалоба на администрацию", discord.ButtonStyle.danger)
        ]
        for label, cat_name, style in categories:
            self.add_item(TicketCategoryButton(label=label, category_name=cat_name, style=style))

# ---------- КОМАНДЫ (доступны только владельцу) ----------
@bot.tree.command(name="ticket_setup", description="Создать сообщение с кнопками")
async def ticket_setup(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Только владелец бота может использовать эту команду.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🎫 ECLIPSE TICKET | Центр поддержки",
        description=(
            "**Нужна помощь, восстановление или разбор ситуации?**\n"
            "Выберите подходящую тему кнопкой ниже, укажите свой **SteamID64** и кратко опишите обращение.\n\n"
            "> ⚠️ **Важно:** создавайте тикет только в подходящей категории — так его быстрее увидит нужная команда.\n\n"
            "❔ **Общие вопросы** — Вопросы по серверам, правилам, донату.\n"
            "📦 **Восстановление имущества** — Потеря вещей из‑за багов, откаты базы, кражи через уязвимости.\n"
            "🛠️ **Технические проблемы** — Ошибки подключения, вылеты, зависания.\n"
            "⚠️ **Жалоба на игрока** — Нарушения правил, конфликты, доказательства.\n"
            "🛡️ **Жалоба на администрацию** — Действия или бездействие администраторов, спорные решения.\n\n"
            "*С уважением, команда ECLIPSE RP*"
        ),
        color=discord.Color.red()
    )
    view = TicketSetupView()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="close", description="Закрыть текущий тикет (только владелец)")
async def close_ticket(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Только владелец бота может использовать эту команду.", ephemeral=True)
        return
    close_btn = CloseTicketButton()
    await close_btn._close(interaction, verified=False)

# ---------- Обработчик сообщений (логирование переписки) ----------
@bot.event
async def on_message(message):
    if message.author.bot:
        await bot.process_commands(message)
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

    await write_ticket_log(ticket_number, f"💬 {message.author}: {message.content}")
    await bot.process_commands(message)

# ---------- Веб-сервер ----------
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

@bot.event
async def on_ready():
    load_counter()
    print(f'✅ Бот {bot.user} запущен! Счётчик: {ticket_counter}')
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        print("⚠️ Бот не на сервере.")
        return
    bot.category = guild.get_channel(TICKET_CATEGORY_ID)
    bot.log_channel = guild.get_channel(LOG_CHANNEL_ID)

    for role_id in ALL_SUPPORT_ROLE_IDS:
        role = guild.get_role(role_id)
        if role:
            print(f"✅ Роль {role.name} (ID: {role_id}) найдена.")
        else:
            print(f"⚠️ Роль с ID {role_id} не найдена.")

    if not bot.category: print(f"⚠️ Категория {TICKET_CATEGORY_ID} не найдена.")
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
