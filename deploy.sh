#!/usr/bin/env bash
# DiscTrack deploy script.
# Run as root on the container: cd /opt/discgolf-tracker && ./deploy.sh
set -euo pipefail

APP_DIR="/opt/discgolf-tracker"
SERVICE="discgolf"
SERVICE_USER="discgolf"

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root (the chown and systemctl steps need it)." >&2
    exit 1
fi

cd "$APP_DIR"

echo "==> Pulling latest code"
OLD_REQ_HASH=$(md5sum requirements.txt | cut -d' ' -f1)
git pull

NEW_REQ_HASH=$(md5sum requirements.txt | cut -d' ' -f1)
if [ "$OLD_REQ_HASH" != "$NEW_REQ_HASH" ]; then
    echo "==> requirements.txt changed, installing dependencies"
    ./venv/bin/pip install -r requirements.txt
else
    echo "==> Dependencies unchanged, skipping pip install"
fi

echo "==> Fixing file ownership"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

echo "==> Restarting $SERVICE"
systemctl restart "$SERVICE"

# Give gunicorn a moment to start (or crash)
sleep 2

echo "==> Service status"
systemctl status "$SERVICE" --no-pager --lines=5 || true

if systemctl is-active --quiet "$SERVICE"; then
    echo
    echo "Deploy complete: $SERVICE is running."
    echo "Reminder: if this update added a column to an existing table, run the"
    echo "manual ALTER TABLE, then re-run: chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR && systemctl restart $SERVICE"
else
    echo
    echo "WARNING: $SERVICE is not active. Check: journalctl -u $SERVICE -n 50" >&2
    exit 1
fi
