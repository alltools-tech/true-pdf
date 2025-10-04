FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# Install system deps: poppler-utils (pdftoppm), ghostscript, qpdf, tesseract, build tools, image format libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    ghostscript \
    qpdf \
    tesseract-ocr \
    libtesseract-dev \
    build-essential \
    libjpeg-dev \
    libwebp-dev \
    libavif-dev \
    libheif-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . /app

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]