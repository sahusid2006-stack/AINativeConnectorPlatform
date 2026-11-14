FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS runtime
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


FROM runtime AS verifier
USER root
COPY tools/requirements.txt /opt/audit-requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /opt/audit-requirements.txt && pip check
COPY fixtures /opt/acceptance
USER 10001
