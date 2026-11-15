FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY build.py ./build.py
COPY src ./src
RUN python build.py --output /app/mcp_lab.pyz && \
    rm -r /app/src /app/build.py && \
    useradd --create-home --uid 10001 mcp
USER mcp
ENTRYPOINT ["python", "/app/mcp_lab.pyz"]
