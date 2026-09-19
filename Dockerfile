FROM docker.io/library/python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt && useradd --create-home --uid 10001 app
COPY --chown=app:app pytest.ini ./
COPY --chown=app:app seems ./seems
COPY --chown=app:app app ./app
COPY --chown=app:app examples ./examples
COPY --chown=app:app tests ./tests
COPY --chown=app:app tools ./tools
COPY --chown=app:app docs ./docs
USER app
ENV PORT=3000 PYTHONPATH=/app
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 CMD python -c "import sys,urllib.request as u; sys.exit(0 if u.urlopen('http://127.0.0.1:3000/api/health', timeout=2).status == 200 else 1)"
CMD ["gunicorn", "--config", "app/gunicorn.conf.py", "app.server:app"]
