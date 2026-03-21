FROM python:3.12-slim

# Install system dependencies for WeasyPrint, fonts, and geospatial libs
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
    postgresql-client \
    fontconfig \
    fonts-liberation \
    fonts-dejavu-core \
    fonts-noto-core \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directories and non-root user
RUN mkdir -p /data/uploads /data/reports /data/backups \
    && useradd -m -u 1000 doc \
    && chown -R doc:doc /data /app

USER doc

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
