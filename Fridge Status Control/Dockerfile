# Dockerfile for Fridge Status Control Service
FROM python:3.11-slim

LABEL maintainer="SmartChill Team"
LABEL version="1.0.0"
LABEL description="Fridge Status Control Service - Temperature and humidity monitoring with malfunction detection"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY StatusControl.py .
COPY MyMQTT.py .
COPY settings.json .

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash fridgestatus && \
    chown -R fridgestatus:fridgestatus /app

USER fridgestatus

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Health check (since this is MQTT-only, we check the process)
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD pgrep -f "StatusControl.py" || exit 1

# Run the service
CMD ["python3", "StatusControl.py"]