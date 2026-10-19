#!/usr/bin/env bash
# Установка бота остатков на сервер Ubuntu 24.04. Запуск от root:
#   curl -fsSL https://raw.githubusercontent.com/Mahovplus/control_stock/main/deploy/ustanovka.sh | bash
# Повторный запуск безопасен: обновляет код и библиотеки, файл .env и базу не трогает.
set -euo pipefail

DIR=/opt/control_stock
REPO=https://github.com/Mahovplus/control_stock.git
SERVICE=ostatki-bot

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите от root (или через sudo)."; exit 1
fi

echo "1/6 Ставлю Python и git..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv git >/dev/null

echo "2/6 Время сервера: Москва (так время в сообщениях бота совпадёт с рестораном)"
timedatectl set-timezone Europe/Moscow

echo "3/6 Пользователь bot: бот работает не от root"
id bot >/dev/null 2>&1 || useradd --system --home "$DIR" --shell /usr/sbin/nologin bot

echo "4/6 Код бота в $DIR"
if [ -d "$DIR/.git" ]; then
  git -c safe.directory="$DIR" -C "$DIR" pull -q --ff-only
else
  git clone -q "$REPO" "$DIR"
fi
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install -q -r "$DIR/requirements.txt"

echo "5/6 Настройки"
if [ ! -f "$DIR/.env" ]; then
  cp "$DIR/.env.example" "$DIR/.env"
  NEW_ENV=1
fi
mkdir -p "$DIR/data"
chown -R bot:bot "$DIR"
chmod 600 "$DIR/.env"  # ключи может читать только бот и root

echo "6/6 Автозапуск"
cat > /etc/systemd/system/$SERVICE.service <<UNIT
[Unit]
Description=Бот учёта остатков
After=network-online.target
Wants=network-online.target

[Service]
User=bot
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python -m bot.main
# Упал: перезапуск через 10 секунд. Код 2 = ошибка в .env, перезапуск не поможет.
Restart=always
RestartSec=10
RestartPreventExitStatus=2

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q $SERVICE

echo
if [ "${NEW_ENV:-}" = 1 ]; then
  echo "Готово. Осталось вписать ключи:  nano $DIR/.env"
  echo "Потом запустить бота:            systemctl restart $SERVICE"
else
  systemctl restart $SERVICE
  echo "Бот обновлён и перезапущен."
fi
echo "Посмотреть, работает ли:        systemctl status $SERVICE"
echo "Последние строки лога:           journalctl -u $SERVICE -n 50"
