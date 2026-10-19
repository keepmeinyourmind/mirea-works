"""Напоминания до срока: за сутки, за 3 часа и о черновике в СДО, который так и не отправили.

Чистая логика без Telegram и базы: какие задания пора напомнить сейчас. Отправляет poller.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import Task

DAY, SOON, DRAFT = "day", "soon", "draft"


@dataclass
class Reminder:
    task: Task
    kind: str  # DAY | SOON | DRAFT
    draft: bool = False  # в СДО черновик — строка задания и так это покажет, отдельно не напоминаем

    def marks(self) -> list[tuple[int, str, datetime]]:
        kinds = [self.kind] + ([DRAFT] if self.draft and self.kind != DRAFT else [])
        return [(self.task.id, k, self.task.deadline) for k in kinds]


def is_quiet(now: datetime, start: int, end: int) -> bool:
    """Ночь: с start часов до end (через полночь, если start > end)."""
    h = now.hour
    return (h >= start or h < end) if start > end else start <= h < end


def due_reminders(tasks: list[Task], sent: set[tuple[int, str, str]], now: datetime,
                  day: timedelta = timedelta(hours=24), soon: timedelta = timedelta(hours=3),
                  quiet: bool = False) -> list[Reminder]:
    """Что напомнить сейчас. sent — уже отправленные (task_id, kind, срок в UTC), quiet — сейчас ночь.

    - «за 3 часа» приходит всегда, даже ночью; после него «за сутки» уже не нужно;
    - «за сутки» ночью ждёт утра;
    - если задание появилось уже внутри окна (добавил сам за 5 часов до срока, бот только что
      нашёл его в СДО) — об этом окне не напоминаем: человек и так только что его видел;
    - черновик в СДО: напоминаем отдельно, если до срока меньше суток и другого напоминания сейчас нет.
    """
    out: list[Reminder] = []
    for t in tasks:
        if t.done or not t.deadline or t.deadline <= now or t.id is None:
            continue
        left = t.deadline - now
        deadline = t.deadline.astimezone(timezone.utc).isoformat()
        was = {kind for kind in (DAY, SOON, DRAFT) if (t.id, kind, deadline) in sent}
        draft = t.status == "draft" and left <= day and DRAFT not in was

        if left <= soon and SOON not in was and not _born_inside(t, soon):
            out.append(Reminder(t, SOON, draft))
        elif left <= day and not was & {DAY, SOON} and not _born_inside(t, day) and not quiet:
            out.append(Reminder(t, DAY, draft))
        elif draft and (not quiet or left <= soon):
            out.append(Reminder(t, DRAFT))
    return out


def _born_inside(t: Task, window: timedelta) -> bool:
    return t.created_at is not None and t.created_at >= t.deadline - window
