FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    YOLO_CONFIG_DIR=/app/.yolo \
    SITUATOR_CONFIG=/app/config

WORKDIR /app

# Зависимости отдельным слоем (кэш сборки)
RUN pip install \
    "ultralytics>=8.3,<9" "opencv-python>=4.10" "numpy>=1.26" \
    "pandas>=2.2" "openpyxl>=3.1" "pyyaml>=6.0" "click>=8.1"

# Веса детектора зашиваются в образ: после сборки контейнер офлайн
RUN mkdir -p /app/weights \
    && cd /app/weights \
    && python -c "from ultralytics import YOLO; YOLO('yolo11s.pt')"

COPY pyproject.toml ./
COPY situator ./situator
RUN pip install --no-deps .

ENTRYPOINT ["situator"]
CMD ["--help"]
