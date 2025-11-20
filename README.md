## Order Bot

Асинхронный Telegram-бот (aiogram 3 + async SQLAlchemy/PostgreSQL) для сбора пожеланий пользователей и управления заявками.

### Основные возможности
- создание и редактирование заявок (товар, бренд, размер, бюджет, комментарии, фото);
- панель администратора (выгрузка отчётов, обновление статусов, push-рассылки, макросы вопросов);
- хранение фотографий в БД с восстановлением файлов при необходимости;
- Prometheus-метрики и структурированные логи;
- выгрузки Excel с выпадающими списками статусов.

### Быстрый старт
```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env  # заполнить BOT_TOKEN и DATABASE_DSN
python main.py
```

### Docker / Compose
```bash
docker compose up -d
# бот + postgres + prometheus + grafana
```
В `docker-compose.yml` можно переопределить переменные через `.env`.

### Метрики и Grafana
- Бот экспортирует Prometheus-метрики на `METRICS_PORT` (по умолчанию 9000) по пути `/metrics`.
- Файл `deploy/prometheus.yml` настраивает сбор метрик; `deploy/grafana-datasources.yml` подключает Prometheus к Grafana (порт 3000).
- Основные метрики:
  - `bot_updates_total`, `bot_update_errors_total`
  - `bot_update_processing_seconds_*`

### DataLens
- Используйте PostgreSQL как источник: настройте read-only пользователя и SSL-доступ.
- В DataLens подключите DSN `postgresql://user:pass@host:port/dbname` и создайте графики на основе таблиц `orders`, `order_photos`, представлений и выгрузок.

### Сборка/деплой
- `.github/workflows/deploy.yml` — шаблон CI/CD (Docker build + деплой через SSH).
- `DEPLOY.md` содержит подробности развёртывания, мониторинга и алертинга.

### Планы
- автоматические тесты;
- отдельные окружения (prod/test) и полноценный CI/CD;
- расширенные аналитические панели.
