"""Источник дедлайнов с портала вуза. Для СДО МИРЭА (Moodle) — см. moodle.py."""

from typing import TYPE_CHECKING, Protocol

from ..config import Settings
from ..models import Task

if TYPE_CHECKING:
    from .moodle_pages import PortalStatus


class PortalAuthError(Exception):
    """Сессия/токен протухли — нужно перелогиниться вручную."""


class PortalSource(Protocol):
    async def fetch(self, cookie: str | None) -> list[Task]:
        """Все актуальные задания пользователя с портала. source="portal", source_id — id задания на портале."""
        ...

    async def check(self, cookie: str | None, tasks: list[Task]) -> dict[int, "PortalStatus"]:
        """Баллы и статус со страниц заданий: {task.id: PortalStatus}."""
        ...


class NullPortal:
    async def fetch(self, cookie: str | None) -> list[Task]:
        return []

    async def check(self, cookie: str | None, tasks: list[Task]) -> dict:
        return {}


def make_portal(settings: Settings) -> PortalSource:
    match settings.portal_kind:
        case "none":
            return NullPortal()
        case "moodle":
            from .moodle import MoodleSessionPortal

            return MoodleSessionPortal(settings.portal_url, settings.portal_lookback_days)
        case other:
            raise ValueError(f"Неизвестный PORTAL_KIND={other!r}")
