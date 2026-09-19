import os

bind = f"0.0.0.0:{os.environ.get('PORT', '3000')}"
workers = 1
worker_class = "gthread"
threads = 16
timeout = 120
graceful_timeout = 5
worker_tmp_dir = "/dev/shm"
accesslog = None
errorlog = "-"
