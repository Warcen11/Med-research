# Medical Research Explorer

Учебное веб-приложение для мультиагентного сбора и анализа научной медицинской информации из **Europe PMC** и **ClinicalTrials.gov**.

## Агенты

1. **Coordinator** запускает сбор информации параллельно, фиксирует ошибки отдельных источников.
2. **Publication Agent** получает публикации и аннотации через Europe PMC REST API.
3. **Trial Agent** получает зарегистрированные клинические испытания через ClinicalTrials.gov API v2.
4. **Analysis Agent** нормализует, агрегирует статистику и связывает записи по NCT ID, если этот ID явно найден в аннотации.

Аналитика алгоритмическая, без генеративной языковой модели. Система не даёт медицинских советов и не оценивает эффективность лечения.

## Локальный запуск

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Развёртывание в Streamlit Community Cloud

1. Загрузите содержимое проекта в репозиторий GitHub.
2. Откройте https://share.streamlit.io/ → **Create app**.
3. Выберите репозиторий, ветку `main` и entrypoint `app.py`.
4. Нажмите **Deploy** и проверьте оба API через интерфейс.

## Ограничения

- Результаты представляют ограниченную выдачу, не систематический обзор.
- Совпадения публикаций и испытаний определяются по упоминаниям NCT ID в заголовке/аннотации, не по смысловой близости.
- Часть записей не содержит аннотаций или опубликованных результатов.
- При сетевой ошибке одного источника продолжается обработка другого.

## API

- https://www.ebi.ac.uk/europepmc/webservices/rest/
- https://clinicaltrials.gov/data-api/api
