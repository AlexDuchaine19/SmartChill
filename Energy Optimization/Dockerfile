# Dockerfile for Energy Optimization Service
FROM python:3.11-slim

LABEL maintainer="SmartChill Team"
LABEL version="2.0.0"
LABEL description="Energy Optimization Service - REST-only with ML capabilities"

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY Optimizer.py .
COPY settings.json .

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash energyopt && \
    chown -R energyopt:energyopt /app

USER energyopt

# Expose REST API port
EXPOSE 8003

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Run the service
CMD ["python3", "Optimizer.py"]