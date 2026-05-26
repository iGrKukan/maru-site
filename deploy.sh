#!/bin/bash
# Авто-деплой: тянет изменения из GitHub и публикует. Запускается launchd каждую минуту.
# Контент (public/) подхватывается мгновенно; при изменении app.py — рестарт сервера.
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin"
cd "$HOME/presentation-site" || exit 0
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
git remote get-url origin >/dev/null 2>&1 || exit 0   # remote не настроен — выходим тихо

before=$(git rev-parse HEAD 2>/dev/null)
git fetch --quiet origin 2>>deploy.log || exit 0
git pull --ff-only --quiet origin main >>deploy.log 2>&1
after=$(git rev-parse HEAD 2>/dev/null)

if [ "$before" != "$after" ]; then
  echo "$(date '+%F %T') deploy $before -> $after" >> deploy.log
  if git diff --name-only "$before" "$after" 2>/dev/null | grep -q '^app\.py$'; then
    launchctl kickstart -k "gui/$(id -u)/com.presentation.site" >>deploy.log 2>&1
    echo "$(date '+%F %T') restarted server (app.py changed)" >> deploy.log
  fi
fi
