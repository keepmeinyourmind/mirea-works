#!/bin/bash
# Выкладка на сервер с Mac:  scripts/deploy.sh
#
# Прогоняет тесты, копирует код и .env на сервер, сохраняет копию базы и перезапускает контейнеры.
# База (data/) и сертификаты на сервере не трогаются. Конфиг Xray лежит на сервере в /root/xray.
set -euo pipefail

HOST="${HOST:-studybot}"                         # из ~/.ssh/config
DOMAIN="${DOMAIN:-mirea-hometask.duckdns.org}"
DIR=/root/studybot
cd "$(dirname "$0")/.."

[ -f .env ] || { echo "Нет .env"; exit 1; }
echo "→ тесты"
uv run pytest -q

echo "→ копирую на $HOST:$DIR"
rsync -az --delete \
  --exclude .venv --exclude data --exclude .git --exclude __pycache__ --exclude .pytest_cache \
  --exclude .DS_Store --exclude design --exclude deploy.env \
  ./ "$HOST:$DIR/"

echo "→ запускаю"
ssh "$HOST" bash -s <<REMOTE
set -euo pipefail
cd $DIR
mkdir -p data
# Копия базы перед обновлением; храним 5 последних
if [ -f data/tasks.sqlite3 ]; then
  cp data/tasks.sqlite3 "data/tasks.backup-\$(date +%Y%m%d-%H%M%S).sqlite3"
  ls -1t data/tasks.backup-*.sqlite3 | tail -n +6 | xargs -r rm --
fi
echo "DOMAIN=$DOMAIN" > deploy.env
docker compose --env-file deploy.env up -d --build --remove-orphans
docker compose --env-file deploy.env ps --format "table {{.Service}}\t{{.Status}}"
REMOTE
echo "Готово: https://$DOMAIN — логи: ssh $HOST 'cd $DIR && docker compose --env-file deploy.env logs -f bot'"
