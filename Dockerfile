# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System deps for OCR + build
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# Workdir
WORKDIR /app

# Copy only requirements first (better caching)
COPY portal/requirements.txt /app/portal/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r /app/portal/requirements.txt \
 && pip install --no-cache-dir gunicorn

# Copy app
COPY . /app

# Create runtime dirs
RUN mkdir -p /app/uploads /app/storage/drafts/raw /app/storage/drafts/.trash /app/uploads/.trash

# Env (no Windows POPPLER_PATH needed on Linux)
ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1 \
    PORT=5000

# Expose port
EXPOSE 5000

# Start with gunicorn (adjust workers per CPU)
# 'portal.app:app' is your Flask app object
CMD ["gunicorn", "-w", "2", "-k", "gthread", "-b", "0.0.0.0:5000", "portal.app:app"]
