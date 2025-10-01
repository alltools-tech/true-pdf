FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# Install system deps: poppler-utils (pdftoppm), ghostscript, qpdf, tesseract, build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    ghostscript \
    qpdf \
    tesseract-ocr \
    libtesseract-dev \
    build-essential \
    libjpeg-dev \
    && rm -rf /var/lib/apt/lists/*

# Create app dir
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy app code
COPY . /app

# Expose
EXPOSE 8000

# Run uvicorn
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
