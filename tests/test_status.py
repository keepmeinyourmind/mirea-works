from study_bot.status import Disk, Host, format_status, human_duration, read_disk, parse_meminfo, read_host

MEMINFO = """MemTotal:         979156 kB
MemFree:           72000 kB
MemAvailable:     401112 kB
SwapTotal:        489468 kB
SwapFree:         437756 kB
"""


def fake_proc(tmp_path, load="0.00 0.02 0.04 1/247 32"):
    (tmp_path / "loadavg").write_text(load)
    (tmp_path / "meminfo").write_text(MEMINFO)
    (tmp_path / "uptime").write_text("135981.65 134324.37")
    return tmp_path


def test_reads_host_from_proc(tmp_path):
    h = read_host(fake_proc(tmp_path))
    assert (h.load5, h.mem_total_mb, h.mem_avail_mb, h.swap_used_mb) == (0.02, 956, 391, 50)
    assert human_duration(h.uptime_s) == "1 д 13 ч"


def test_no_proc_on_mac(tmp_path):
    assert read_host(tmp_path / "нет") is None


def test_parse_meminfo():
    assert parse_meminfo(MEMINFO)["MemAvailable"] == 401112


def disk(used_gb, total_gb=15):
    return Disk(total_gb * 2**30, used_gb * 2**30, (total_gb - used_gb) * 2**30)


def text(host=None, d=None, **kw):
    args = dict(rss_mb=120, db_mb=0.1, users=3, users_sdo=2, users_expired=0, open_tasks=20, bot_uptime_s=3600)
    args.update(kw)
    return format_status(host, d, **args)


def test_healthy_status(tmp_path):
    t = text(read_host(fake_proc(tmp_path)), disk(2.6))
    assert "свободно 391 из 956 МБ" in t and "занято 2.6 из 15 ГБ (17%)" in t
    assert "Пользователи</b>: 3, СДО подключено у 2" in t and "✅ Всё в порядке" in t


def test_warnings(tmp_path):
    h = Host(load1=2.5, load5=1.8, load15=1.2, cpus=1, mem_total_mb=956, mem_avail_mb=60, swap_used_mb=300, uptime_s=60)
    t = text(h, disk(13), users_expired=1)
    assert t.count("⚠️") == 3 and "сессия истекла у 1" in t
    assert "мало памяти" in t and "процессор не успевает" in t and "диск заполнен" in t


def test_mac_without_server_data():
    t = text(None, None, rss_mb=None)
    assert "Процессор" not in t and "Бот</b>: работает 1 ч 0 мин" in t


def test_read_disk(tmp_path):
    d = read_disk(tmp_path)
    assert d.total > 0 and d.used + d.free <= d.total
