# image-organizer-ai
# CPU-only image; torch is installed from the CPU index to keep it small.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# libgl1/libglib2.0-0: needed by opencv (pulled in by ultralytics)
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install -r requirements.txt

COPY . .

# Pre-download the default model so the container works without
# hitting the network on first run.
RUN python -c "from organizer import get_model; get_model('yolo11n.pt', '/models'); print('model ready')"

ENV MODEL_DIR=/models \
    ORGANIZER_ROOTS=/data \
    ORGANIZER_DATA=/app/data \
    PORT=5000

VOLUME ["/data", "/app/data"]
EXPOSE 5000

CMD ["python", "app.py"]
