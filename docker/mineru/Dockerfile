FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "magic-pdf[full]" \
    fastapi \
    "uvicorn[standard]" \
    python-multipart

COPY main.py .

EXPOSE 7999
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7999"]
