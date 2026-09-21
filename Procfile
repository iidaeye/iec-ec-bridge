release: python manage.py migrate --noinput
web: gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 2 --timeout 60
worker: python manage.py run_worker
