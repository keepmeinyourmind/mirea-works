"""Состояние сервера и бота для команды /status.

Читаем /proc и диск: в Docker-контейнере они показывают весь сервер (ядро общее), так что цифры —
те же, что `uptime`, `free` и `df` по SSH. На Mac /proc нет — тогда показываем только данные бота.
"""

import os
import shutil
import time
from dataclasses import dataclass
from typing import NamedTuple
from html import escape
from pathlib import Path

PROC = Path("/proc")
# Пороги, после которых /status предупреждает
LOW_MEM_MB = 100
HIGH_LOAD = 1.0  # на 1 ядре: больше 1 — процессор не успевает
HIGH_DISK = 0.8

STARTED = time.time()


@dataclass
class Host:
    load1: float
    load5: float
    load15: float
    cpus: int
    mem_total_mb: int
    mem_avail_mb: int
    swap_used_mb: int
    uptime_s: float


class Disk(NamedTuple):
    total: int
    used: int
    free: int


def read_disk(path: str | Path) -> Disk | None:
    try:
        return Disk(*shutil.disk_usage(path))
    except OSError:
        return None


def parse_meminfo(text: str) -> dict[str, int]:
    """«MemAvailable:  401112 kB» → {"MemAvailable": 401112} (в кБ)."""
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if rest.strip():
            out[key] = int(rest.split()[0])
    return out


def read_host(proc: Path = PROC) -> Host | None:
    try:
        load = (proc / "loadavg").read_text().split()
        mem = parse_meminfo((proc / "meminfo").read_text())
        uptime = float((proc / "uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None  # не Linux (Mac) — данных сервера нет
    return Host(
        load1=float(load[0]), load5=float(load[1]), load15=float(load[2]),
        cpus=os.cpu_count() or 1,
        mem_total_mb=mem["MemTotal"] // 1024,
        mem_avail_mb=mem["MemAvailable"] // 1024,
        swap_used_mb=(mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)) // 1024,
        uptime_s=uptime,
    )


def bot_rss_mb(proc: Path = PROC) -> int | None:
    try:
        for line in (proc / "self" / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def human_duration(seconds: float) -> str:
    d, rest = divmod(int(seconds), 86400)
    h, rest = divmod(rest, 3600)
    m = rest // 60
    if d:
        return f"{d} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def format_status(host: Host | None, disk: Disk | None, rss_mb: int | None,
                  db_mb: float, users: int, users_sdo: int, users_expired: int, open_tasks: int,
                  bot_uptime_s: float) -> str:
    lines = ["<b>📊 Состояние</b>", ""]
    warn: list[str] = []
    if host:
        per_cpu = host.load5 / host.cpus
        lines.append(f"<b>Процессор</b>: нагрузка {host.load1:.2f} / {host.load5:.2f} / {host.load15:.2f} "
                     f"(1/5/15 мин, ядер: {host.cpus})")
        lines.append(f"<b>Память</b>: свободно {host.mem_avail_mb} из {host.mem_total_mb} МБ"
                     + (f", swap занят {host.swap_used_mb} МБ" if host.swap_used_mb else ""))
        lines.append(f"<b>Сервер работает</b>: {human_duration(host.uptime_s)}")
        if host.mem_avail_mb < LOW_MEM_MB:
            warn.append(f"мало памяти (меньше {LOW_MEM_MB} МБ) — пора думать о тарифе с 2 ГБ")
        if per_cpu > HIGH_LOAD:
            warn.append("процессор не успевает (нагрузка за 5 минут выше числа ядер)")
    if disk:
        share = disk.used / disk.total
        lines.append(f"<b>Диск</b>: занято {disk.used / 2**30:.1f} из {disk.total / 2**30:.0f} ГБ ({share:.0%})")
        if share > HIGH_DISK:
            warn.append("диск заполнен больше чем на 80% — почисти старые образы: docker image prune -f")
    lines += [
        "",
        f"<b>Бот</b>: работает {human_duration(bot_uptime_s)}"
        + (f", память {rss_mb} МБ" if rss_mb is not None else "") + f", база {db_mb:.1f} МБ",
        f"<b>Пользователи</b>: {users}, СДО подключено у {users_sdo}"
        + (f", сессия истекла у {users_expired}" if users_expired else ""),
        f"<b>Открытых заданий</b>: {open_tasks}",
        "",
        "⚠️ " + "\n⚠️ ".join(escape(w) for w in warn) if warn else "✅ Всё в порядке",
    ]
    return "\n".join(lines)
