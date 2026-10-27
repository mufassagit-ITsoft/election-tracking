FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_DIR=/data \
    POETRY_VIRTUALENVS_CREATE=false POETRY_NO_INTERACTION=1
WORKDIR /app
RUN pip install --no-cache-dir poetry
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main
COPY . .
RUN mkdir -p /data && SECRET_KEY=build-only DEBUG=False ALLOWED_HOSTS=localhost python manage.py collectstatic --noinput
EXPOSE 8000
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py load_seed && gunicorn config.wsgi:application -b 0.0.0.0:8000 --workers 2"]
