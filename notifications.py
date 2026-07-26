import discord
from datetime import datetime

async def send_close_notification(creator, ticket_number, open_time, close_time, verified, closer):
    """
    Отправляет пользователю ЛС с информацией о закрытии тикета.
    
    Параметры:
        creator: discord.Member или discord.User (создатель тикета)
        ticket_number: int
        open_time: datetime (время открытия)
        close_time: datetime (время закрытия)
        verified: bool (был ли тикет проверен)
        closer: discord.Member (кто закрыл)
    """
    try:
        reason = "Вопрос решен" if verified else "Тикет закрыт"
        color = discord.Color.green() if verified else discord.Color.orange()

        embed = discord.Embed(
            title=f"# Тикет #{ticket_number:05d} закрыт",
            color=color
        )
        embed.add_field(name="Открыл тикет", value=creator.mention, inline=False)
        embed.add_field(name="Закрыл тикет", value=closer.mention, inline=False)
        embed.add_field(
            name="Тикет открыт",
            value=open_time.strftime("%d %B %Y г. %H:%M"),
            inline=False
        )
        embed.add_field(
            name="Тикет закрыт",
            value=close_time.strftime("%d %B %Y г. %H:%M"),
            inline=False
        )
        embed.add_field(name="Причина закрытия", value=reason, inline=False)
        embed.set_footer(text=close_time.strftime("%d.%m.%Y %H:%M"))

        await creator.send(embed=embed)
    except Exception as e:
        print(f"⚠️ Не удалось отправить ЛС пользователю {creator.id}: {e}")
