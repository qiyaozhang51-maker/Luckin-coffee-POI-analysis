FROM python:3.11-slim

WORKDIR /app

# Install minimal system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy and install API-only dependencies (lightweight, fast build)
COPY requirements-api.txt /app/requirements-api.txt
RUN pip install --no-cache-dir -r requirements-api.txt

# Copy backend code as a package
COPY ./backend /app/backend
# Copy scripts for data pipeline
COPY ./scripts /app/scripts

# Set Python path
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
