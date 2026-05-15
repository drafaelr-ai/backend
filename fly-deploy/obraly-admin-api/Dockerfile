FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements_admin.txt .
RUN pip install --no-cache-dir -r requirements_admin.txt

COPY logging_setup.py .
COPY config_admin.py .
COPY extensions_admin.py .
COPY models_admin/ models_admin/
COPY services_admin/ services_admin/
COPY routes_admin/ routes_admin/
COPY app_admin.py .

EXPOSE 8080

CMD ["gunicorn", "app_admin:app", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "120"]
