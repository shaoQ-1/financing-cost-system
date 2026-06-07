# ── Build stage ──
FROM docker.1ms.run/python:3.11-slim AS builder

WORKDIR /app
COPY requirements.txt .
ENV http_proxy=http://192.168.0.110:7890
ENV https_proxy=http://192.168.0.110:7890
RUN pip install --no-cache-dir --user -r requirements.txt
ENV http_proxy=
ENV https_proxy=

# ── Runtime stage ──
FROM docker.1ms.run/python:3.11-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /root/.local

# Install Chinese font + Tesseract-OCR + PaddleOCR system deps
ENV http_proxy=http://192.168.0.110:7890
ENV https_proxy=http://192.168.0.110:7890
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-microhei \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra \
    && rm -rf /var/lib/apt/lists/*
ENV http_proxy=
ENV https_proxy=

# Allow matplotlib to find the font
ENV MATPLOTLIBRC=/app

# Copy application code
COPY . .

# Ensure writable directories exist
RUN mkdir -p /app/sessions /app/uploads

ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

EXPOSE 5120

CMD ["python", "-m", "waitress", "--call", "--host=0.0.0.0", "--port=5120", "--threads=8", "--channel-timeout=300", "--recv-bytes=65536", "--connection-limit=20", "app:create_app"]
