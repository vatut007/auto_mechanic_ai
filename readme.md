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

## Быстрый старт

```bash
cp .env.example .env      # и вписать ключ провайдера
docker compose up -d      # Open WebUI → http://localhost:3000
```

### Ядро (Этап 1)

Тулза Python — [uv](https://docs.astral.sh/uv/): `.venv` и зависимости создаёт `uv sync`,
любые команды — через `uv run` (см. бриф §8 и правило 6 в плане).

```bash
uv sync                                            # создать .venv + uv.lock (обновляет)
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- `http://localhost:8000/healthz` — состояние реестра и ключей
- Open WebUI → Admin Panel → Settings → Connections → OpenAI API → «+»:
  Base URL `http://host.docker.internal:8000/v1`, любой ключ, Auth Type: Bearer
- Модели-алиасы провайдеров появятся в списке моделей; смена провайдера = правка
  [config/providers.yaml](config/providers.yaml), ноль кода
- QA-прогон: `uv run python scripts/qa_run.py --model deepseek-chat`
  (отчёт ляжет в `data/qa_results/`)
- Тесты: `uv run pytest tests/ -q`

### Конвейер документов (Этап 2)

```bash
# положить PDF в data/inbox/ и:
uv run python -m app.pipeline ingest    # разобрать новые файлы
uv run python -m app.pipeline status    # что в манифесте, кто ждёт разбора
```

- Приёмка: SHA-256 → манифест (`data/pipeline.db`), дубликаты по хешу пропускаются;
  замок от двойного запуска (`data/pipeline.lock`), второй триггер отклоняется.
- Метаданные из имени файла по каталогу моделей [config/models_catalog.json](config/models_catalog.json);
  минимум для приёмки: бренд + модель + поколение + тип, иначе `needs_review`
  (файл остаётся в `inbox/`, причина — в `status` и в манифесте).
- Артефакты: `data/processed/<doc_id>/` — `chunks.jsonl` (чанки ~800 знаков,
  перекрытие ~200), `tables/*.json` (каждая таблица — атомарный объект; моментные
  помечены `needs_manual_check`), `images/*.png` (300 dpi, мусор отфильтрован) + карточки.
- Принятые файлы конвейер переносит в `data/library/<бренд>/<модель>/<поколение>/<тип>/`.
- OCR для сканов без текстового слоя — отдельный путь, позже (не в основном конвейере).

Телефон в том же Wi-Fi: `http://<ip-машины>:3000`.
Встроенный RAG «Знания» Open WebUI — потрогать формат на паре PDF из `data/inbox/` (загрузка через UI).

## Сквозные правила

1. Секреты — только в локальном `.env`, в git никогда.
2. `data/` — вне git; оригиналы PDF — источник истины.
3. Ответ агента без источника (документ/раздел/страница) = баг, а не ответ.
