#!/data/data/com.termux/files/usr/bin/bash
set -euo pipefail

pkg update -y
pkg install -y python git termux-api

[ -d "$HOME/deal-watch" ] || git clone https://github.com/SebasDrewes/deal-watch "$HOME/deal-watch"
cd "$HOME/deal-watch"
chmod +x tools/termux/*.sh

ENV="$HOME/.config/deal-watch.env"
if [ ! -f "$ENV" ]; then
  mkdir -p "$HOME/.config"
  read -rp "Gmail address (sends and receives alerts): " addr
  read -rsp "Gmail app password: " pw; echo
  umask 077
  printf 'DEAL_WATCH_EMAIL_TO=%s\nDEAL_WATCH_SMTP_USER=%s\nELECTROOUTLET_SMTP_PASSWORD=%s\n' \
    "$addr" "$addr" "$pw" > "$ENV"
fi

tools/termux/job.sh --test-email
tools/termux/job.sh --bootstrap
tail -3 watch.log

mkdir -p "$HOME/.termux/boot"
ln -sf "$HOME/deal-watch/tools/termux/schedule.sh" "$HOME/.termux/boot/deal-watch"
tools/termux/schedule.sh
