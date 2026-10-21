FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/apps/api
WORKDIR /app
RUN groupadd --gid 10001 datamind && useradd --uid 10001 --gid 10001 --create-home datamind
COPY pyproject.toml alembic.ini ./
COPY apps/api ./apps/api
RUN pip install --no-cache-dir . && mkdir -p /app/data/files /app/data/sources /app/data/models && chown -R 10001:10001 /app/data
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "datamind.api:app", "--host", "0.0.0.0", "--port", "8000"]
