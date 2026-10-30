#!/usr/bin/env bash
# Выкладка сайта на сервер: API, модуль подбора и веб-часть с каталогом → образ pcb-api на сервере.
# Базу не везёт: сайт работает по выгрузке каталога. После нового сбора цен пересобрать каталог
# (Windows: .venv-win/Scripts/python.exe -m collector.catalog) и запустить снова.
# Сервер один раз подготовлен: Docker + compose, подкачка 1 ГБ, ufw — открыты 22, 80 и 443.
# Домен pcpodbor.ru (A-записи @ и www на сервер; старый albion-x.shop у reg.ru перенаправляется); HTTPS — Caddy, см. deploy/Caddyfile.
# Запуск из WSL: scripts/deploy.sh [root@хост]
set -euo pipefail
HOST=${1:-root@135.106.211.80}
KEY=${SSH_KEY:-$HOME/.ssh/id_ed25519}
cd "$(dirname "$0")/.."

tar czf - --exclude=__pycache__ \
    api/Dockerfile api/requirements.txt api/main.py \
    collector/__init__.py collector/perf.py collector/advisor.py \
    web deploy/docker-compose.yml deploy/Caddyfile |
  ssh -i "$KEY" -o BatchMode=yes "$HOST" '
    set -e
    mkdir -p /opt/pc-builder && cd /opt/pc-builder
    rm -rf web            # удалённые локально файлы не должны остаться в образе
    tar xzf -
    docker compose -f deploy/docker-compose.yml up -d --build --remove-orphans
    docker image prune -f >/dev/null'
echo "готово: http://${HOST#*@}"
