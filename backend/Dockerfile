FROM python:3.12-slim

# Install system dependencies for WeasyPrint and geospatial libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libcairo2 \
    libglib2.0-0 \
    libgeos-dev \
    libproj-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories and non-root user
RUN mkdir -p /data/uploads /data/reports \
    && useradd -m -u 1000 droneops \
    && chown -R droneops:droneops /data /app

USER droneops

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
