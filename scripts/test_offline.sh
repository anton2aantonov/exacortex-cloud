#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
XAGON_ALLOW_LIVE_API=0 PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/cloud_checks.py
