FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml /app/
RUN python - <<'PY'
import pathlib
import tomllib
data = tomllib.loads(pathlib.Path("pyproject.toml").read_text())
deps = list(data["project"]["dependencies"])
deps += list(data["project"]["optional-dependencies"]["telegram"])
pathlib.Path("/tmp/requirements-runtime.txt").write_text("\n".join(deps) + "\n")
PY
RUN pip install --no-cache-dir -r /tmp/requirements-runtime.txt

COPY app /app/app
COPY khqr_sdk /app/khqr_sdk
COPY alembic /app/alembic
COPY alembic.ini /app/alembic.ini
RUN pip install --no-cache-dir --no-deps .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
