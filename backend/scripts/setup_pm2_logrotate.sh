#!/usr/bin/env bash
# setup_pm2_logrotate.sh — Install and configure pm2-logrotate
# Run manually once: bash scripts/setup_pm2_logrotate.sh
set -e

pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 14
pm2 set pm2-logrotate:compress true
pm2 set pm2-logrotate:rotateInterval '0 0 * * *'

echo "✓ pm2-logrotate configured: 10 MB max, 14-day retention, daily rotation, compressed."
