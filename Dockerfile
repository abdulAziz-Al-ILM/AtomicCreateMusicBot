FROM python:3.11-slim

WORKDIR /app

# System packages
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY main.py .

# Bot token from environment
ENV PYTHONUNBUFFERED=1

# Run bot
CMD ["python", "main.py"]
