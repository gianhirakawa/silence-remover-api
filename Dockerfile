FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install ffmpeg, curl, AND fontconfig (needed for fc-list in your code)
RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    curl \
    fontconfig \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create working directory
WORKDIR /app

# Copy requirements first (for caching)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create temp directory for processing
RUN mkdir -p /tmp/videos && chmod 777 /tmp/videos

# NOTE: EXPOSE is for documentation only. Heroku ignores this.
EXPOSE 5000

# Health check (Good for local/AWS, Heroku uses its own)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# --------------------------------------------------------------------------------
# THE CRITICAL CHANGE FOR HEROKU + CLEANUP
# 1. We use 'sh -c' to run multiple commands
# 2. We start the cleaning loop in the background (&)
# 3. We bind Gunicorn to the dynamic $PORT provided by Heroku
# --------------------------------------------------------------------------------
CMD sh -c "mkdir -p /tmp/videos && \
           (while true; do find /tmp/videos -type f -mmin +60 -delete; sleep 600; done) & \
           gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 600 app:app"