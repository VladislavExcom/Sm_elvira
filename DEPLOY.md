# Deployment Guide

## 1. Build & Run Locally with Docker

```bash
# copy .env with BOT_TOKEN / DATABASE_DSN etc. if needed
docker build -t order-bot .
docker run --rm \
  -e BOT_TOKEN=... \
  -e DATABASE_DSN=postgresql+asyncpg://user:pass@db:5432/orders_db \
  -e METRICS_PORT=9000 \
  -p 9000:9000 \
  order-bot
```

Persist `photos`/`tmp` by mounting volumes if требуются локальные файлы.

## 2. Метрики и Grafana

- Приложение экспортирует метрики Prometheus на `METRICS_PORT` (по умолчанию `9000`) с путём `/metrics`.
- Подключите Prometheus к контейнеру/серверу и добавьте datasource в Grafana.
- Основные метрики:
  - `bot_updates_total{event_type=...}`
  - `bot_update_errors_total`
  - `bot_update_processing_seconds_bucket`

## 3. Логи

- Логи выводятся в `stdout` с единым форматом (`timestamp level logger message`). Собирайте их через любимый лог-агрегатор (journald, Loki и т.д.).
- Настраивайте уровень через `LOG_LEVEL`.

## 4. Prod Checklist

1. Создайте PostgreSQL (можно Managed) и выкатите миграции (`init_db` создаёт таблицы автоматически).
2. Настройте `BOT_TOKEN`, `DATABASE_DSN`, `PHOTOS_DIR`, `TMP_DIR`, `METRICS_PORT`.
3. Организуйте резервное копирование БД; при необходимости вынесите `photos`/`tmp` в S3.
4. Настройте мониторинг:
   - Prometheus → Grafana (дашборд с количеством обновлений, ошибками, временем обработки).
   - Алерт при росте ошибок.
5. Подумайте про reverse proxy (Traefik/Nginx) если будете использовать вебхуки.

## 5. Удаление мусора

Не храните тестовые файлы в образе: `.dockerignore` исключает `photos`, `tmp`, временные `*.xlsx` и `*.log`. Перед билдом очистите эти каталоги.
