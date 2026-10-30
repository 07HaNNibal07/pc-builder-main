# Веб-сервер системы: API подбора + статика. Базе данных не нужен — работает по выгрузке каталога,
# поэтому переносится на любой сервер вместе с web/prototype-3d/catalog.js.
FROM python:3.12-slim
WORKDIR /app
COPY api/requirements.txt api/
RUN pip install --no-cache-dir -r api/requirements.txt
COPY collector/__init__.py collector/perf.py collector/advisor.py collector/
COPY api/ api/
COPY web/ web/
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
