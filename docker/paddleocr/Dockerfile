FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir paddlepaddle
RUN pip install --no-cache-dir paddleocr
RUN pip install --no-cache-dir fastapi uvicorn[standard] python-multipart

COPY main.py .
EXPOSE 7998
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7998"]