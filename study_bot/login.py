"""Одноразовый вход userbot'а. Запускать локально (нужен ввод кода из Telegram):

    uv run python -m study_bot.login

Создаёт data/userbot.session и печатает твой id и id супергрупп — их вписать в .env.
Файл .session = доступ к аккаунту. Не коммить и никому не отдавай.
"""

import asyncio

from telethon import TelegramClient
from telethon.tl.types import Channel

from .config import Settings


async def main() -> None:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(str(s.session_path), s.api_id, s.api_hash)
    await client.start()  # спросит телефон, код и пароль 2FA, если есть

    me = await client.get_me()
    print(f"\nOWNER_ID={me.id}\n\nТвои супергруппы (id для CHAT_IDS):")
    async for d in client.iter_dialogs():
        if isinstance(d.entity, Channel) and d.entity.megagroup:
            print(f"  {d.id:>16}  {d.name}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
