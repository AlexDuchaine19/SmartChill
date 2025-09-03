FROM python:3.11-slim

WORKDIR /app

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Health check - verify both InfluxDB and Catalog connectivity
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import requests; requests.get('http://catalog:8001/health', timeout=5); requests.get('http://influxdb:8086/health', timeout=5)" || exit 1

# Run the application
CMD ["python", "InfluxDB_Adaptor.py"]