FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV TREVOLTO_DATA_DIR=/data
VOLUME /data
EXPOSE 8000
# Set TREVOLTO_SECRET_KEY (KMS-backed) and TREVOLTO_PUBLIC_URL at run time.
CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
