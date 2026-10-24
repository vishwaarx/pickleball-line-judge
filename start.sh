#!/bin/zsh
# Start the line judge. Usage: ./start.sh [--host IP] [--no-speak]
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
cd server
if [ ! -x .venv/bin/python ]; then
  echo "Creating Python environment…"
  uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -e .
fi
mkdir -p logs
echo "Starting… logs in server/logs/pickle.log"
exec .venv/bin/python app.py "$@"
