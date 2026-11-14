FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    useradd --create-home --uid 10001 rag
COPY src ./src
USER rag
ENTRYPOINT ["python", "-m", "rag.database_cli"]
CMD ["--help"]
