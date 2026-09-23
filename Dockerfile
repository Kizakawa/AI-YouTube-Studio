# Dockerfile chuẩn hóa cho AI YouTube Studio
# Hỗ trợ: Render.com, Hugging Face Spaces, Koyeb, Docker VPS, Oracle Cloud
FROM python:3.11-slim

# Thiết lập biến môi trường
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8000 \
    FFMPEG_THREADS=2

# Cài đặt FFmpeg và font chữ quốc tế
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    fonts-dejavu-core \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Tạo sẵn toàn bộ cây thư mục lưu trữ cần thiết
RUN mkdir -p /app/storage/temp /app/storage/outputs /app/storage/jobs /app/credentials/accounts

# Cài đặt thư viện Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn vào image
COPY . .

# Expose cổng Web Dashboard (mặc định 8000)
EXPOSE 8000

# Khởi chạy bot và web dashboard song song
CMD ["python", "run.py"]
