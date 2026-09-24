#!/data/data/com.termux/files/usr/bin/bash
D="$HOME/deal-watch/tools/termux"
termux-job-scheduler --job-id 1 --period-ms 900000 --network any --persisted true --script "$D/quick.sh"
termux-job-scheduler --job-id 2 --period-ms 10800000 --network any --persisted true --script "$D/full.sh"
termux-job-scheduler --pending
