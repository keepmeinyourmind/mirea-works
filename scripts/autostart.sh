#!/bin/bash
# Автозапуск бота на Mac через launchd: стартует при входе в систему, поднимается после падения.
#
#   scripts/autostart.sh install    — поставить и запустить
#   scripts/autostart.sh uninstall  — остановить и убрать
#   scripts/autostart.sh status     — работает ли
#   scripts/autostart.sh logs       — последние строки лога
#
# Бот запускается через `caffeinate -i`: пока он работает, Mac не засыпает от бездействия.
# Закрытая крышка всё равно усыпляет Mac — тогда бот догонит пропущенное после пробуждения.
set -euo pipefail

LABEL="ru.studybot.bot"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$PROJECT/data/bot.log"
DOMAIN="gui/$(id -u)"

# bootout асинхронный: ждём, пока старая служба действительно выгрузится, иначе bootstrap падает с ошибкой 5
stop_service() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || return 0
  for _ in $(seq 1 20); do
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || return 0
    sleep 0.5
  done
}

case "${1:-}" in
  install)
    [ -f "$PROJECT/.env" ] || { echo "Нет $PROJECT/.env — сначала заполни его (см. .env.example)"; exit 1; }
    UV="$(command -v uv || true)"
    [ -n "$UV" ] || { echo "Не найден uv — установи: https://docs.astral.sh/uv/"; exit 1; }
    if pgrep -f "python -m study_bot.main" >/dev/null && ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      echo "Бот уже запущен вручную (в терминале). Останови его (Ctrl+C) и повтори — два бота с одним токеном конфликтуют."
      exit 1
    fi
    mkdir -p "$PROJECT/data" "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$UV</string><string>run</string>
    <string>python</string><string>-m</string><string>study_bot.main</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$(dirname "$UV"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <!-- Если упал (например, нет сети после пробуждения) — повторить не чаще раза в 30 секунд -->
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST
    stop_service
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Готово: бот запущен и будет стартовать при входе в систему. Лог: $LOG"
    ;;
  uninstall)
    stop_service
    rm -f "$PLIST"
    echo "Автозапуск убран, бот остановлен."
    ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl print "$DOMAIN/$LABEL" | awk '/state =|pid =|last exit code/ {gsub(/^[ \t]+/, ""); print}'
    else
      echo "Автозапуск не установлен."
    fi
    ;;
  logs)
    tail -n "${2:-40}" "$LOG" 2>/dev/null || echo "Лога пока нет."
    ;;
  *)
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
