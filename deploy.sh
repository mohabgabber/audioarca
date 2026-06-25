#!/bin/bash
set -euo pipefail

service_name="${1:-web}"
environment_name="${2:-dev}"
port="${3:-8000}"

export DEBUG="${DEBUG:-false}"
export DATABASE_HOST="${DATABASE_HOST:-${DB_HOST:-postgres}}"
export DATABASE_PORT="${DATABASE_PORT:-${DB_PORT:-5432}}"
export CELERY_BROKER_URL="${CELERY_BROKER_URL:-${REDIS_URL:-redis://redis:6379/0}}"
export CELERY_BEAT_SCHEDULE_FILENAME="${CELERY_BEAT_SCHEDULE_FILENAME:-/tmp/celerybeat-schedule}"

wait_for_tcp() {
    local host="$1"
    local port="$2"
    local label="$3"
    echo "Waiting for ${label} (${host}:${port})"
    while ! nc -z "$host" "$port"; do
        sleep 1
    done
}

wait_for_tcp "$DATABASE_HOST" "$DATABASE_PORT" "PostgreSQL"

broker_host="$(python - <<'PY'
from urllib.parse import urlparse
import os

print(urlparse(os.environ["CELERY_BROKER_URL"]).hostname or "redis")
PY
)"
broker_port="$(python - <<'PY'
from urllib.parse import urlparse
import os

print(urlparse(os.environ["CELERY_BROKER_URL"]).port or 6379)
PY
)"
wait_for_tcp "$broker_host" "$broker_port" "Celery broker"

if [ "$environment_name" = "dev" ]; then
    export DEBUG=true
fi

mkdir -p "$(dirname "$CELERY_BEAT_SCHEDULE_FILENAME")"

if [ "$service_name" = "web" ]; then
    python manage.py migrate --noinput
    if [ "$environment_name" = "dev" ]; then
        python manage.py runserver 0.0.0.0:"$port"
    else
        python manage.py collectstatic --clear --noinput
        gunicorn -c gunicorn.py
    fi
elif [ "$service_name" = "worker" ]; then
    celery -A core worker -l info
elif [ "$service_name" = "beat" ]; then
    celery -A core beat -l info --schedule="$CELERY_BEAT_SCHEDULE_FILENAME"
elif [ "$service_name" = "flower" ]; then
    celery -A core flower --port="$port"
else
    echo "Unknown service name: $service_name"
    exit 1
fi
