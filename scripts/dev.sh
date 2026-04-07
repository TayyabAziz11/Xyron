#!/bin/bash
# Start AI Operator in development mode

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "AI Operator starting from: $REPO_ROOT"

# Start backend API
cd "$REPO_ROOT/backend"
if [ -d ".venv" ]; then
  source .venv/bin/activate
elif [ -d "../.venv" ]; then
  source ../.venv/bin/activate
fi

python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!
echo "Backend API started (PID: $BACKEND_PID) on http://localhost:8000"

# Start web frontend
cd "$REPO_ROOT/web"
npm run dev &
FRONTEND_PID=$!
echo "Web dashboard started (PID: $FRONTEND_PID) on http://localhost:3000"

echo ""
echo "AI Operator running:"
echo "  Dashboard: http://localhost:3000"
echo "  API:       http://localhost:8000"
echo "  API Docs:  http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all services"

cleanup() {
  echo "Stopping services..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

wait
