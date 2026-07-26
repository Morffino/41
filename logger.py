import os
import asyncio
from datetime import datetime

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Блокировка для предотвращения одновременной записи
log_lock = asyncio.Lock()

def get_log_path(ticket_number: int) -> str:
    """Возвращает путь к лог-файлу тикета."""
    return os.path.join(LOG_DIR, f"ticket-{ticket_number:05d}.log")

async def write_log(ticket_number: int, line: str):
    """Асинхронно дописывает строку в лог-файл."""
    async with log_lock:
        path = get_log_path(ticket_number)
        # Добавляем временную метку
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {line}\n")

async def log_open(ticket_number: int, user: str, category: str, steamid: str, nickname: str, problem: str):
    """Запись открытия тикета."""
    await write_log(ticket_number, f"🟢 ТИКЕТ ОТКРЫТ")
    await write_log(ticket_number, f"   Пользователь: {user}")
    await write_log(ticket_number, f"   Категория: {category}")
    await write_log(ticket_number, f"   SteamID64: {steamid}")
    await write_log(ticket_number, f"   Ник в игре: {nickname}")
    await write_log(ticket_number, f"   Проблема: {problem}")

async def log_message(ticket_number: int, author: str, content: str):
    """Запись сообщения пользователя."""
    # Пропускаем сообщения от бота (проверка в main)
    await write_log(ticket_number, f"💬 {author}: {content}")

async def log_close(ticket_number: int, closer: str, verified: bool):
    """Запись закрытия тикета."""
    status = "ПРОВЕРЕН" if verified else "ЗАКРЫТ"
    await write_log(ticket_number, f"🔴 ТИКЕТ {status}")
    await write_log(ticket_number, f"   Закрыл: {closer}")

async def get_log_content(ticket_number: int) -> str:
    """Читает содержимое лог-файла."""
    path = get_log_path(ticket_number)
    if not os.path.exists(path):
        return "Лог пуст."
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

async def delete_log(ticket_number: int):
    """Удаляет лог-файл после отправки."""
    path = get_log_path(ticket_number)
    if os.path.exists(path):
        os.remove(path)
