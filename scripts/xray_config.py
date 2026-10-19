#!/usr/bin/env python3
"""Собирает конфиг Xray (клиент VLESS + Reality) из ссылки vless://. Запускается на сервере.

Если подписка сменила ключ, ссылку обновляют так (с Mac, ссылка — в буфере обмена):
    pbpaste | ssh studybot 'cat > /root/vless.txt && chmod 600 /root/vless.txt'
    ssh studybot 'python3 /root/studybot/scripts/xray_config.py && cd /root/studybot && docker compose --env-file deploy.env restart xray'

Секреты из ссылки скрипт не печатает. Только стандартная библиотека — на сервере ничего ставить не нужно.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

LINK = Path("/root/vless.txt")
OUT = Path("/root/xray/config.json")
XRAY_UID = 65532  # пользователь, от которого работает контейнер xray


def build(link: str) -> dict:
    u = urlsplit(link.strip())
    if u.scheme != "vless" or not u.username or not u.hostname:
        raise ValueError("это не ссылка vless://uuid@host:port?...")
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    if q.get("security") != "reality":
        raise ValueError(f"поддерживается только security=reality, а в ссылке {q.get('security')!r}")
    user = {"id": unquote(u.username), "encryption": "none"}
    if q.get("flow"):
        user["flow"] = q["flow"]
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {"tag": "http", "listen": "0.0.0.0", "port": 10809, "protocol": "http"},
            {"tag": "socks", "listen": "0.0.0.0", "port": 10808, "protocol": "socks", "settings": {"udp": False}},
        ],
        "outbounds": [{
            "tag": "vless",
            "protocol": "vless",
            "settings": {"vnext": [{"address": u.hostname, "port": u.port or 443, "users": [user]}]},
            "streamSettings": {
                "network": q.get("type", "tcp"),
                "security": "reality",
                "realitySettings": {
                    "serverName": q.get("sni", ""),
                    "fingerprint": q.get("fp", "chrome"),
                    "publicKey": q.get("pbk", ""),
                    "shortId": q.get("sid", ""),
                    "spiderX": unquote(q.get("spx", "")),
                },
            },
        }],
    }


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else LINK
    try:
        config = build(src.read_text())
    except (OSError, ValueError) as e:
        sys.exit(f"Не получилось: {e}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    OUT.chmod(0o600)
    os.chown(OUT, XRAY_UID, XRAY_UID)
    v = config["outbounds"][0]["settings"]["vnext"][0]
    print(f"Готово: {OUT} → сервер {v['address']}:{v['port']} (ключи не показываю)")


if __name__ == "__main__":
    main()
