FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml poetry.lock bot.py /app/
COPY butler /app/butler

RUN pip install --no-cache-dir .

CMD ["butler"]
