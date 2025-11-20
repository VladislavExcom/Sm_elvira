FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY bot ./bot
COPY main.py .

RUN pip install --upgrade pip \
    && pip install .

ENV METRICS_PORT=9000 \
    LOG_LEVEL=INFO

CMD ["python", "main.py"]
