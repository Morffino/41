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

# ---------- Логирование (минимальное) ----------
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

async def write_ticket_log(ticket_number: int, text: str):
    path = os.path.join(LOG_DIR, f"ticket-{ticket_number:05d}.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")

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

bot.category = None
bot.support_role = None
bot.log_channel = None
bot.active_tickets = {}

CATEGORIES = [
    ("Общие вопросы", "general", "❓", discord.ButtonStyle.primary),
    ("Восстановление вещей", "restore", "📦", discord.ButtonStyle.success),
    ("Технические проблемы", "tech", "🔧", discord.ButtonStyle.secondary),
    ("Жалоба на игрока/группировку", "player_report", "⚠️", discord.ButtonStyle.danger),
    ("Жалоба на Администрацию", "admin_report", "🚨", discord.ButtonStyle.danger)
]

# ---------- Модальное окно (без create_task) ----------
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
        # 1. Мгновенный defer (снимает 3-секундный лимит)
        await interaction.response.defer(ephemeral=True)

        # 2. ВСЯ ЛОГИКА ВЫПОЛНЯЕТСЯ СИНХРОННО (но асинхронно)
        try:
            steam = self.steamid.value.strip()
            if not steam.isdigit():
                await interaction.followup.send("❌ SteamID64 – только цифры.", ephemeral=True)
                return
            if len(steam) > 20:
                await interaction.followup.send("❌ Слишком длинный SteamID.", ephemeral=True)
                return

            guild = interaction.guild
            category = bot.category
            support_role = bot.support_role
            if not category or not support_role:
                await interaction.followup.send("❌ Ошибка конфигурации сервера.", ephemeral=True)
                return

            # Проверка существующего тикета
            if interaction.user.id in bot.active_tickets:
                ch = bot.active_tickets[interaction.user.id]
                if ch and ch.guild == guild:
                    await interaction.followup.send(f"⚠️ У вас уже есть тикет: {ch.mention}", ephemeral=True)
                    return

            # Получаем номер
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

            # Создаём канал (без таймаута)
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=str(interaction.user.id)
            )

            bot.active_tickets[interaction.user.id] = channel

            # Логируем
            await write_ticket_log(current_number, f"Тикет создан {interaction.user} (ID:{interaction.user.id})")
            await write_ticket_log(current_number, f"Категория: {self.category_name}")
            await write_ticket_log(current_number, f"SteamID64: {steam}")
            await write_ticket_log(current_number, f"Ник: {self.nickname.value}")
            await write_ticket_log(current_number, f"Проблема: {self.brief.value}")

            # Отправляем embed
            embed = discord.Embed(title="📋 Информация", color=discord.Color.blue())
            embed.add_field(name="Категория", value=self.category_name, inline=False)
            embed.add_field(name="SteamID64", value=steam, inline=False)
            embed.add_field(name="Ник", value=self.nickname.value, inline=False)
            embed.add_field(name="Проблема", value=self.brief.value, inline=False)
            embed.set_footer(text=f"От: {interaction.user.display_name}")
            await channel.send(embed=embed)

            # Кнопки управления
            view = discord.ui.View()
            view.add_item(CloseTicketButton())
            view.add_item(VerifyTicketButton())
            await channel.send("🔒 Кнопки управления:", view=view)

            # Уведомление в лог-канал
            log_channel = bot.log_channel
            if log_channel:
                try:
                    await log_channel.send(f"🆕 Тикет #{current_number:05d} от {interaction.user.mention} → {channel.mention}")
                except:
                    pass

            # Финальный ответ пользователю
            await interaction.followup.send(f"✅ Тикет создан! {channel.mention}", ephemeral=True)

        except Exception as e:
            print(f"❌ Ошибка: {e}")
            try:
                await interaction.followup.send("❌ Внутренняя ошибка.", ephemeral=True)
            except:
                pass

# ---------- Кнопки ----------
class TicketCategoryButton(discord.ui.Button):
    def __init__(self, label: str, category_name: str, emoji: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style, custom_id=f"ticket_{category_name}", emoji=emoji)
        self.category_name = category_name
    async def callback(self, interaction: discord.Interaction):
        modal = TicketModal(category_name=self.category_name)
        await interaction.response.send_modal(modal)

class CloseTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="close_ticket", emoji="🔒")
    async def callback(self, interaction: discord.Interaction):
        await self._close(interaction, False)
    async def _close(self, interaction: discord.Interaction, verified: bool):
        channel = interaction.channel
        if not channel.category or channel.category.id != TICKET_CATEGORY_ID:
            await interaction.response.send_message("❌ Не канал тикета.", ephemeral=True)
            return
        creator_id = channel.topic
        if creator_id is None:
            await interaction.response.send_message("❌ Ошибка создателя.", ephemeral=True)
            return
        creator_id = int(creator_id)
        if interaction.user.id != creator_id and not interaction.user.get_role(SUPPORT_ROLE_ID):
            await interaction.response.send_message("⛔ Нет прав.", ephemeral=True)
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
                log_msg += f" админом {interaction.user} (ник: {admin})"
            await write_ticket_log(ticket_number, log_msg)
            log_channel = bot.log_channel
            if log_channel:
                log_content = await read_ticket_log(ticket_number)
                if log_content.strip():
                    temp_path = f"/tmp/ticket_{ticket_number:05d}.log"
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(log_content)
                    try:
                        await log_channel.send(
                            f"📄 Лог #{ticket_number:05d} ({status})",
                            file=discord.File(temp_path, filename=f"ticket_{ticket_number:05d}.log")
                        )
                    except:
                        pass
                    os.remove(temp_path)
            await delete_ticket_log(ticket_number)
        await channel.delete()
        if creator_id in bot.active_tickets:
            del bot.active_tickets[creator_id]

class VerifyTicketButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Тикет проверен", style=discord.ButtonStyle.success, custom_id="verify_ticket", emoji="✅")
    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.get_role(SUPPORT_ROLE_ID):
            await interaction.response.send_message("⛔ Только для поддержки.", ephemeral=True)
            return
        close_btn = CloseTicketButton()
        await close_btn._close(interaction, True)

# ---------- Представление ----------
class TicketSetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for label, ident, emoji, style in CATEGORIES:
            self.add_item(TicketCategoryButton(label=label, category_name=label, emoji=emoji, style=style))
ticket_setup_view = TicketSetupView()

# ---------- Команды ----------
@bot.tree.command(name="ticket_setup", description="Создать сообщение с кнопками")
@app_commands.default_permissions(administrator=True)
async def ticket_setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 ECLIPSE TICKET | Центр поддержки",
        description=(
            "**Нужна помощь?** Выберите тему:\n\n"
            "❔ **Общие вопросы** – правила, донат\n"
            "📦 **Восстановление имущества** – откаты, кражи\n"
            "🛠️ **Технические проблемы** – ошибки, вылеты\n"
            "⚠️ **Жалоба на игрока** – нарушения\n"
            "🛡️ **Жалоба на администрацию** – спорные действия"
        ),
        color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, view=ticket_setup_view)

@bot.tree.command(name="close", description="Закрыть текущий тикет")
async def close_ticket(interaction: discord.Interaction):
    close_btn = CloseTicketButton()
    await close_btn._close(interaction, False)

# ---------- Обработчик сообщений ----------
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
    await write_ticket_log(ticket_number, f"{message.author} (ID:{message.author.id}): {message.content}")
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
