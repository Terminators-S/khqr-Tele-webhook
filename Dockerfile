FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml /app/
COPY app /app/app
COPY khqr_sdk /app/khqr_sdk
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
RUN pip install --no-cache-dir ".[telegram]"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
