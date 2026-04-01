FROM python:3.12-slim

# Install system dependencies for WeasyPrint, fonts, geospatial libs, and gosu
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
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y passlib 2>/dev/null || true

COPY . .

# Create data directories and non-root user
RUN mkdir -p /data/uploads /data/reports /data/backups \
    && useradd -m -u 1000 doc \
    && chown -R doc:doc /data /app

# Entrypoint script: fix volume permissions as root, then exec as doc
RUN printf '#!/bin/sh\n\
# Fix /data ownership — Docker named volumes may retain root ownership\n\
chown -R doc:doc /data 2>/dev/null || true\n\
exec gosu doc "$@"\n' > /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
