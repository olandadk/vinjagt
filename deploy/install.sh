#!/usr/bin/env bash
# Installerer Vinjagt på en Raspberry Pi (Raspberry Pi OS Bookworm/Bullseye, også Pi 3B).
# Kør fra mappen med det klonede repository:   bash deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${SUDO_USER:-$USER}"
PORT="${VINJAGT_PORT:-8080}"
RUN_AT="${VINJAGT_TIME:-04:15}"

echo "==> Installerer pakker (bruger færdige Debian-pakker – intet skal kompileres på Pi'en)"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-requests python3-bs4 python3-yaml python3-lxml git

if [ ! -f "$APP_DIR/config.yaml" ]; then
  cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"
  echo "==> config.yaml oprettet – husk at skrive din e-mail i user_agent"
fi
mkdir -p "$APP_DIR/data" "$APP_DIR/site" "$APP_DIR/ratings"

echo "==> Opretter systemd-tjenester"
sudo tee /etc/systemd/system/vinjagt.service >/dev/null <<EOF
[Unit]
Description=Vinjagt – scan danske vinforhandlere og byg hjemmesiden
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 -m vinjagt run
Nice=10
IOSchedulingClass=idle
MemoryMax=600M
TimeoutStartSec=6h
EOF

sudo tee /etc/systemd/system/vinjagt.timer >/dev/null <<EOF
[Unit]
Description=Kør Vinjagt hver nat

[Timer]
OnCalendar=*-*-* $RUN_AT:00
RandomizedDelaySec=20min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo tee /etc/systemd/system/vinjagt-web.service >/dev/null <<EOF
[Unit]
Description=Vinjagt – lokal webserver på port $PORT
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 -m vinjagt serve --port $PORT
Restart=on-failure
MemoryMax=80M

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vinjagt.timer vinjagt-web.service

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
cat <<EOF

Færdig!
  Hjemmeside:          http://${IP:-raspberrypi.local}:$PORT
  Kør scanning nu:     sudo systemctl start vinjagt.service   (første gang kan tage 1-3 timer)
  Følg loggen:         journalctl -u vinjagt -f
  Status pr. shop:     python3 -m vinjagt status
  Næste planlagte:     systemctl list-timers vinjagt.timer

Udgivelse til GitHub/GitLab/Codeberg: se README.md, afsnittet "Udgiv hjemmesiden".
EOF
