FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_admin.txt .
RUN pip install --no-cache-dir -r requirements_admin.txt

# Factory pattern — copy all admin modules
COPY app_admin.py .
COPY auto_migration_admin.py .
COPY config_admin.py .
COPY extensions_admin.py .
COPY logging_setup.py .
COPY services_admin/ ./services_admin/
COPY models_admin/ ./models_admin/
COPY routes_admin/ ./routes_admin/

EXPOSE 8080

CMD ["gunicorn", "app_admin:app", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
