FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY kv_store /app/kv_store

ENV PYTHONPATH=/app

CMD ["python", "-m", "kv_store.app.main"]
