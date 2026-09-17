FROM python:3.12-slim

# Set to true to bake in the Azure CLI, which lets the container reuse an
# `az login` session mounted from the host (see docker-compose.yml). Leave
# false for managed-identity or service-principal hosts — those need no CLI
# and keep the image ~400MB smaller.
ARG WITH_AZURE_CLI=false

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY app/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Optional Azure CLI, for hosts that authenticate with `az login`
RUN if [ "$WITH_AZURE_CLI" = "true" ]; then pip install --no-cache-dir azure-cli; fi

# Copy app code
COPY app/ .

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Expose port
EXPOSE 8080

# Run gunicorn (--no-sendfile avoids socket issues in containers)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--timeout", "300", "--no-sendfile", "--access-logfile", "-", "--error-logfile", "-", "--config", "/dev/null", "app:app"]
