# auto_mechanic_ai

Личный AI-помощник автомеханика: RAG-база по мануалам (VAG / Lada / StarLine, позже Changan),
чат через готовый UI, воркфлоу и журнал работ. Не для продажи, база наружу не выкладывается.

## Документы

- Бриф проекта: [docs/project-brief.md](docs/project-brief.md)
- План по этапам: [docs/implementation-plan.md](docs/implementation-plan.md)
- Решения (ADR): [docs/decisions.md](docs/decisions.md)
- Чек-лист сбора PDF: [docs/document-sources.md](docs/document-sources.md)

## Структура репозитория

```
app/       — ядро (FastAPI, появляется на Этапе 1)
config/    — конфиги (реестр LLM-провайдеров)
docs/      — бриф, план, решения, чек-листы
data/      — PDF/индексы/кейсы — ВНЕ git, бэкапится отдельно
  inbox/     ← сюда вручную кладутся новые файлы (единственное ручное действие)
  library/   ← раскладывает конвейер (Этап 2)
  cases/     — карточки опыта мастеров
  equipment/ — каталог оборудования сервиса
```

## Быстрый старт (Этап 0)

```bash
cp .env.example .env      # и вписать ключ провайдера
docker compose up -d      # Open WebUI → http://localhost:3000
```

Телефон в том же Wi-Fi: `http://<ip-машины>:3000`.
Встроенный RAG «Знания» Open WebUI — потрогать формат на паре PDF из `data/inbox/` (загрузка через UI).

## Сквозные правила

1. Секреты — только в локальном `.env`, в git никогда.
2. `data/` — вне git; оригиналы PDF — источник истины.
3. Ответ агента без источника (документ/раздел/страница) = баг, а не ответ.
