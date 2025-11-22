FROM python:3.10-slim

# Audio ishlashi uchun ffmpeg o'rnatamiz
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Kutubxonalarni o'rnatamiz
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kodlarni ko'chiramiz
COPY . .

# Vaqtinchalik papka ochamiz
RUN mkdir -p downloads

CMD ["python", "main.py"]