#!/data/data/com.termux/files/usr/bin/bash
set -a
. "$HOME/.config/deal-watch.env"
set +a
cd "$HOME/deal-watch" || exit 1
exec python watch.py "$@" >> job.out.log 2>&1
