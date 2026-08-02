#!/usr/bin/env bash
# deploy.sh — publish the browser build to the VPS as static files.
#
# Usage:   browser/deploy/deploy.sh                 # af@144.202.111.184
#          browser/deploy/deploy.sh af@other.host
#
# This is deliberately NOT _roadcase/template/deploy/deploy.sh. That script
# builds a venv and restarts a systemd unit because the template deploys a
# Flask app. Nothing runs here: the browser build is static files, and the
# radio is on the viewer's machine, so the VPS only ever hands over HTML and
# JavaScript. No unit, no venv, no port, no state to wedge.
#
# Re-running this is the whole update story — rsync --delete makes the
# webroot match the repo exactly, and the nginx config sends Cache-Control:
# no-cache so a browser cannot keep serving yesterday's band table.
#
# ── FIRST DEPLOY ON A NEW HOST (once) ───────────────────────────────────
#   0. DNS:  rf.alexthe5th.com  A  ->  144.202.111.184     <- do this first
#            Nothing below can succeed without it: certbot proves control
#            of the name by being reached over it.
#   1. Run this script. It creates the webroot and copies the files.
#   2. sudo install -d -m 755 /var/www/acme          # if not already there
#      sudo cp browser/deploy/nginx-rf.conf /etc/nginx/sites-available/rf-scanner
#      sudo ln -s /etc/nginx/sites-available/rf-scanner /etc/nginx/sites-enabled/
#      sudo nginx -t && sudo systemctl reload nginx
#   3. sudo certbot certonly --webroot -w /var/www/acme -d rf.alexthe5th.com
#      sudo certbot renew --dry-run    # the step that actually proves it
#
# Step 2's `nginx -t` runs against the WHOLE config, showrunner included.
# If it fails, do not reload — a bad reload takes the status page down with
# it.
set -euo pipefail

cd "$(dirname "$0")/.."                       # browser/
HOST="${1:-af@144.202.111.184}"
WEBROOT=/var/www/rf-scanner
STAGE=/tmp/rf-scanner-stage

# spike.html is the protocol bench harness — it belongs at the desk, not on
# the public origin. deploy/ is this script and the nginx config.
#
# `--exclude '.*'` is not paranoia. Everything gitignored is INVISIBLE to
# `git status` but perfectly visible to rsync, so without this line the
# webroot gets whatever tooling happened to leave behind — a stray
# browser/.pytest_cache from a pytest run with the wrong cwd was already
# staged for publication once (2026-08-01) before this was added. Anything
# the app actually needs is a normal file; a dotfile in here is litter.
#
# `--delete-excluded`, not plain `--delete`: rsync PROTECTS excluded paths
# on the destination, so with `--delete` alone anything junk that ever
# reached the target would be pinned there permanently by the very rule
# meant to keep it out. Caught the same day, staging the fix for the line
# above.
rsync -av --delete --delete-excluded \
  --exclude '.*' --exclude deploy --exclude spike.html \
  ./ "$HOST:$STAGE/"

# Staged through /tmp because rsync cannot sudo on the far end.
#
# `ssh -t` is load-bearing: this box has no passwordless sudo (checked
# 2026-08-01), so without a TTY sudo cannot prompt and the whole second
# half fails with "a password is required". It also means this script has
# to be run by a human who knows the password — it cannot be automated
# from a hook or a cron job as written.
ssh -t "$HOST" "sudo install -d -m 755 '$WEBROOT' \
  && sudo rsync -a --delete '$STAGE/' '$WEBROOT/' \
  && sudo chown -R root:root '$WEBROOT' \
  && sudo chmod -R a+rX '$WEBROOT' \
  && rm -rf '$STAGE' \
  && echo '=> published to $WEBROOT' \
  && ls -la '$WEBROOT'"
