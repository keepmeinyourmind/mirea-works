"""Источник дедлайнов с портала вуза. Для СДО МИРЭА (Moodle) — см. moodle.py.

Ручная проверка: uv run python -m study_bot.sources.portal
"""

import asyncio
from typing import Protocol

from ..config import Settings
from ..models import Task


class PortalAuthError(Exception):
    """Сессия/токен протухли — нужно перелогиниться вручную."""


class PortalSource(Protocol):
    async def fetch(self) -> list[Task]:
        """Все актуальные задания с портала. source="portal", source_id — id задания на портале."""
        ...


class NullPortal:
    async def fetch(self) -> list[Task]:
        return []


def make_portal(settings: Settings) -> PortalSource:
    match settings.portal_kind:
        case "none":
            return NullPortal()
        case "moodle":
            from .moodle import MoodleSessionPortal

            return MoodleSessionPortal(settings.portal_url, settings.portal_cookie_path, settings.portal_lookback_days)
        case other:
            raise ValueError(f"Неизвестный PORTAL_KIND={other!r}")


async def _main() -> None:
    portal = make_portal(Settings())
    for t in await portal.fetch():
        print(f"{t.deadline:%d.%m %H:%M}" if t.deadline else "без срока", "|", t.subject, "|", t.title, "|", t.url)


if __name__ == "__main__":
    asyncio.run(_main())
