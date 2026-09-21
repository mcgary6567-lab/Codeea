FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PROMETHEUS_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
# Set PROMETHEUS_SECRET_KEY (KMS-backed) and PROMETHEUS_PUBLIC_URL at run time.
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
