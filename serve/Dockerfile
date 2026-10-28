FROM python:3.12-slim

WORKDIR /app

COPY serve/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ api/
COPY agent/ agent/
COPY app/ /app/frontend/

ENV STATIC_DIR=/app/frontend \
    DATA_DIR=/data \
    PYTHONPATH=/app

EXPOSE 8654

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8654"]
