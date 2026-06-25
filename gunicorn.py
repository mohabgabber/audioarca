import multiprocessing

wsgi_app = "core.wsgi:application"
workers = multiprocessing.cpu_count() * 2 + 1  # Balance CPU bound vs I/O bound
bind = "0.0.0.0:8000"

# Emit logs to stdout/stderr so container runtimes capture them
accesslog = "-"
errorlog = "-"

pidfile = "/tmp/gunicorn.pid"
capture_output = True
daemon = False
timeout = 30
graceful_timeout = 30
max_requests = 500
max_requests_jitter = 50
worker_tmp_dir = "/dev/shm"
