#!/usr/bin/env bash
# Upload the COMMITTED code (git HEAD) to the server and restart the services.
# Run from the repo root on the PC (Git Bash):
#   deploy/deploy.sh <server-ip> <path-to-private-key>
# Only tracked files are sent (git archive): .env, live_state/ and the data cache never
# leave the PC, and the server's live_state/ is left untouched.
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "usage: deploy/deploy.sh <server-ip> <path-to-private-key>" >&2
  exit 1
fi
HOST="ubuntu@$1"
KEY="$2"
APP=/opt/xfb
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "$HOST")

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "warning: uncommitted changes are NOT deployed (only git HEAD is)." >&2
fi

"${SSH[@]}" "sudo mkdir -p $APP && sudo chown ubuntu:ubuntu $APP"
git archive --format=tar HEAD | "${SSH[@]}" "tar -x -C $APP"
echo "code uploaded: $(git rev-parse --short HEAD)"

# After the first setup_server.sh run: refresh deps and restart what's running.
"${SSH[@]}" "if [ -x $APP/.venv/bin/pip ]; then $APP/.venv/bin/pip install -q -r $APP/requirements.txt; fi;
  for s in xfb-bot xfb-dashboard; do
    if systemctl is-active --quiet \$s; then sudo systemctl restart \$s && echo restarted \$s; fi
  done"
