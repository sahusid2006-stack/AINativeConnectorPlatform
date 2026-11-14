FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && pip check \
    && useradd --uid 10001 --create-home app
COPY backend/connector_studio ./connector_studio
COPY backend/migrations ./migrations
COPY contracts ./contracts
COPY tests/backend ./tests
COPY fixtures ./fixtures
USER 10001
CMD ["sh", "-c", "if [ \"$SERVICE_ROLE\" = api ]; then python -m connector_studio.database migrate || exit 1; fi; exec python -m uvicorn connector_studio.main:app --host 0.0.0.0 --port 8000 --no-access-log"]
