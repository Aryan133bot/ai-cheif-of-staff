FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY ["dashboard/requirements.txt", "/app/dashboard/requirements.txt"]
COPY ["email processor/requirements.txt", "/app/email_processor/requirements.txt"]

# Install Python dependencies
RUN pip install --no-cache-dir -r /app/dashboard/requirements.txt \
    && pip install --no-cache-dir -r /app/email_processor/requirements.txt

# Copy application code
COPY ["dashboard/", "/app/dashboard/"]
COPY ["email processor/", "/app/email_processor/"]

# Rename directory to remove space (avoids path issues in Linux)
# The server.py references "../email processor" so we create a symlink
RUN ln -s /app/email_processor "/app/email processor"

# Create data directory for SQLite DB and tokens
RUN mkdir -p /app/data

# Set environment defaults
ENV PYTHONUNBUFFERED=1
ENV DASHBOARD_DB_PATH=/app/data/phase1_tasks.db
ENV HOST=0.0.0.0
ENV PORT=8000

WORKDIR /app/dashboard

EXPOSE 8000

CMD ["python", "server.py"]
