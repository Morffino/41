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
ADMIN_CHANNEL_ID = int(os.getenv('ADMIN_CHANNEL_ID', 0))

if not all([TOKEN, TICKET_CATEGORY_ID, SUPPORT_ROLE_ID, LOG_CHANNEL_ID, ADMIN_CHANNEL_ID]):
    print("❌ Ошибка: не заданы все переменные окружения.")
    print("Необходимы: DISCORD_TOKEN, TICKET_CATEGORY_ID, SUPPORT_ROLE_ID, LOG_CHANNEL_ID, ADMIN_CHANNEL_ID")
    sys.exit(1)

# ---------- Счётчик тикетов (сохраняется в файл) ----------
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

# ---------- Логирование (асинхронный буфер) ----------
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
log_buffer = {}
log_lock = asyncio.Lock()

async def _flush_logs():
    """Фоновый сброс буфера логов на диск раз в 5 секунд."""
    while True:
        await asyncio.sleep(5)
        async with log_lock:
            for num, lines in list(log_buffer.items()):
                if lines:
                    path = os.path.join(LOG_DIR, f"ticket-{num:05d}.log")
                    with open(path, "a", encoding="utf-8") as f:
                        f.write("\n".join(lines) + "\n")
                    log_buffer[num] = []
            for num in list(log_buffer.keys()):
                if not log_buffer[num]:
                    del log_buffer[num]

async def write_ticket_log(ticket_number: int, text: str):
    """Быстрое добавление записи в буфер."""
    async with log_lock:
        if ticket_number not in log_buffer:
            log_buffer[ticket_number] = []
        log_buffer[ticket_number].append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}")

async def read_ticket_log(ticket_number: int) -> str:
    path = os.path.join(LOG_DIR, f"ticket-{ticket_number:05d}.log")
    if not os.path.exists(path):
        return "Лог пуст."
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

async def delete_ticket_log(ticket_number: int):
    path = os.path.join(LOG_DIR, f"ticket-{ticket_number:05d}.log")
    if os.path.exists(path):
        os.remove(path)

# ---------- Бот ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Кэш объектов (чтобы не искать каждый раз)
bot.category = None
bot.support_role = None
bot.log_channel = None
bot.admin_channel = None
bot.active_tickets = {}  # user_id -> channel

# ---------- Категории (эмодзи + цвета) ----------
CATEGORIES = [
    ("Общие вопросы", "general", "❓", discord.ButtonStyle.primary),
    ("Восстановление вещей", "restore", "📦", discord.ButtonStyle.success),
    ("Технические проблемы", "tech", "🔧", discord.ButtonStyle.secondary),
    ("Жалоба на игрока/группировку", "player_report", "⚠️", discord.ButtonStyle.danger),
    ("Жалоба на Администрацию", "admin_report", "🚨", discord.ButtonStyle.danger)
]

# ---------- Модальное окно (максимально лёгкое) ----------
class TicketModal(discord.ui.Modal, title='📩 Создание тикета'):
    steamid = discord.ui.TextInput(
        label='SteamID64',
        placeholder='Только цифры',
        required=True
    )
    nickname = discord.ui.TextInput(
        label='Ник в игре',
        placeholder='Укажите ник',
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
        # Мгновенный ответ Discord (снимает 3-секундный таймаут)
        await interaction.response.defer(ephemeral=True)
        # Запуск фоновой обработки
        asyncio.create_task(self._handle(interaction))

    async def _handle(self, interaction: discord.Interaction):
        try:
            steam = self.steamid.value.strip()
            if not steam.isdigit():
                await interaction.followup.send("❌ SteamID64 должен содержать только цифры.", ephemeral=True)
                return
            if len(steam) > 20:
                await interaction.followup.send("❌ SteamID64 слишком длинный.", ephemeral=True)
                return

            guild = interaction.guild
            category = bot.category
            support_role = bot.support_role

            if not category or not support_role:
                await interaction.followup.send("❌ Ошибка конфигурации сервера.", ephemeral=True)
                return

            # Проверка, есть ли уже открытый тикет (кэш)
            if interaction.user.id in bot.active_tickets:
                ch = bot.active_tickets[interaction.user.id]
                if ch and ch.guild == guild:
                    await interaction.followup.send(f"⚠️ У вас уже есть открытый тикет: {ch.mention}", ephemeral=True)
                    return

            # Получаем номер
            async with counter_lock:
                current_number = ticket_counter
                ticket_counter += 1
                save_counter()

            # Создаём канал (с таймаутом 5 секунд)
            channel_name = f"ticket-{current_number:05d}"
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
                support_role: discord.PermissionOverwrite(view_channel=True, send_messages=True)
            }
            try:
                channel = await asyncio.wait_for(
                    guild.create_text_channel(
                        name=channel_name,
                        category=category,
                        overwrites=overwrites,
                        topic=str(interaction.user.id)
                    ),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                await interaction.followup.send("❌ Тайм-аут создания канала. Попробуйте позже.", ephemeral=True)
                async with counter_lock:
                    ticket_counter = current_number
                    save_counter()
                return
            except Exception as e:
                await interaction.followup.send(f"❌ Ошибка создания канала: {e}", ephemeral=True)
                async with counter_lock:
                    ticket_counter = current_number
                    save_counter()
                return

            # Сохраняем в кэш активных тикетов
            bot.active_tickets[interaction.user.id] = channel

            # Логирование (в буфер)
            await write_ticket_log(current_number, f"Тикет создан {interaction.user} (ID:{interaction.user.id})")
            await write_ticket_log(current_number, f"Категория: {self.category_name}")
            await write_ticket_log(current_number, f"SteamID64: {steam}")
            await write_ticket_log(current_number, f"Ник: {self.nickname.value}")
            await write_ticket_log(current_number, f"Проблема: {self.brief.value}")

            # Embed в канале тикета
            embed = discord.Embed(title="📋 Информация о тикете", color=discord.Color.blue())
            embed.add_field(name="Категория", value=self.category_name, inline=False)
            embed.add_field(name="SteamID64", value=steam, inline=False)
            embed.add_field(name="Ник", value=self.nickname.value, inline=False)
            embed.add_field(name="Проблема", value=self.brief.value, inline=False)
            embed.set_footer(text=f"От: {interaction.user.display_name}")
            await channel.send(embed=embed)

            # Кнопки управления тикетом
            view = discord.ui.View()
            view.add_item(CloseTicketButton())
            view.add_item(VerifyTicketButton())
            await channel.send("🔒 Для закрытия нажмите кнопку.", view=view)

            # Уведомление в лог-канал (краткое)
            log_channel = bot.log_channel
            if log_channel:
                try:
                    await log_channel.send(f"🆕 Тикет #{current_number:05d} от {interaction.user.mention} в {channel.mention}")
                except:
                    pass

            # Ответ пользователю
            await interaction.followup.send(f"✅ Тикет создан! Перейдите в {channel.mention}", ephemeral=True)

        except Exception as e:
            await interaction.followup.send("❌ Внутренняя ошибка. Попробуйте позже.", ephemeral=True)
            print(f"Ошибка в _handle: {e}")

# ---------- Кнопка категории ----------
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

        await interaction.response.send_message("⏳ Закрытие...", ephemeral=True)

        if ticket_number:
            status = "ПРОВЕРЕН" if verified else "ЗАКРЫТ"
            admin = interaction.user.display_name if verified else ""
            log_msg = f"Тикет {status}"
            if verified:
                log_msg += f" администратором {interaction.user} (ник: {admin})"
            await write_ticket_log(ticket_number, log_msg)

            # Принудительный сброс буфера для этого тикета
            async with log_lock:
                if ticket_number in log_buffer and log_buffer[ticket_number]:
                    path = os.path.join(LOG_DIR, f"ticket-{ticket_number:05d}.log")
                    with open(path, "a", encoding="utf-8") as f:
                        f.write("\n".join(log_buffer[ticket_number]) + "\n")
                    del log_buffer[ticket_number]

            # Отправка лога в лог-канал
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
            await delete_ticket_log(ticket_number)

        # Удаляем канал
        await channel.delete()
        # Удаляем из кэша
        if creator_id in bot.active_tickets:
            del bot.active_tickets[creator_id]

# ---------- Кнопка "Тикет проверен" ----------
class VerifyTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Тикет проверен", style=discord.ButtonStyle.success, custom_id="verify_ticket", emoji="✅")

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.get_role(SUPPORT_ROLE_ID):
            await interaction.response.send_message("⛔ Доступно только для поддержки.", ephemeral=True)
            return
        close_btn = CloseTicketButton()
        await close_btn._close(interaction, verified=True)

# ---------- Представление с кнопками (кэшированное) ----------
class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, ident, emoji, style in CATEGORIES:
            self.add_item(TicketCategoryButton(label=label, category_name=label, emoji=emoji, style=style))

ticket_setup_view = TicketSetupView()

# ---------- Команда /ticket_setup (только в админ-канале) ----------
@bot.tree.command(name="ticket_setup", description="Создать сообщение с кнопками (только админы)")
@app_commands.default_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    if interaction.channel_id != ADMIN_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ Эта команда доступна только в специальном канале для администраторов.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="🎫 ECLIPSE TICKET | Центр поддержки",
        description=(
            "**Нужна помощь?**\nВыберите тему кнопкой ниже.\n\n"
            "❔ **Общие вопросы** – Вопросы по серверу, правилам, донату.\n"
            "📦 **Восстановление имущества** – Откаты, кражи, потеря вещей.\n"
            "🛠️ **Технические проблемы** – Ошибки, вылеты, зависания.\n"
            "⚠️ **Жалоба на игрока / группировку** – Нарушения правил.\n"
            "🛡️ **Жалоба на администрацию** – Спорные действия."
        ),
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, view=ticket_setup_view)

# ---------- Команда /close (для закрытия) ----------
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
    # Логируем сообщение (в буфер)
    await write_ticket_log(ticket_number, f"{message.author} (ID:{message.author.id}): {message.content}")
    await bot.process_commands(message)

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
    print("🌐 Health check запущен на порту 8080")
    await asyncio.Event().wait()

# ---------- Событие готовности ----------
@bot.event
async def on_ready():
    load_counter()
    print(f'✅ Бот {bot.user} запущен! Счётчик: {ticket_counter}')
    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        print("⚠️ Бот не состоит ни на одном сервере.")
        return

    bot.category = guild.get_channel(TICKET_CATEGORY_ID)
    bot.support_role = guild.get_role(SUPPORT_ROLE_ID)
    bot.log_channel = guild.get_channel(LOG_CHANNEL_ID)
    bot.admin_channel = guild.get_channel(ADMIN_CHANNEL_ID)

    if not bot.category:
        print(f"⚠️ Категория с ID {TICKET_CATEGORY_ID} не найдена.")
    if not bot.support_role:
        print(f"⚠️ Роль с ID {SUPPORT_ROLE_ID} не найдена.")
    if not bot.log_channel:
        print(f"⚠️ Лог-канал с ID {LOG_CHANNEL_ID} не найден.")
    if not bot.admin_channel:
        print(f"⚠️ Админ-канал с ID {ADMIN_CHANNEL_ID} не найден.")

    # Запускаем фоновую задачу сброса логов
    asyncio.create_task(_flush_logs())

    try:
        synced = await bot.tree.sync()
        print(f"🔄 Синхронизировано {len(synced)} команд.")
    except Exception as e:
        print(f"⚠️ Ошибка синхронизации команд: {e}")

# ---------- Запуск бота и веб-сервера ----------
async def main():
    asyncio.create_task(start_web_server())
    await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
