FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive

# Install all dependencies and fonts in one RUN for smaller layers and better cache
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
    libreoffice \
    fonts-noto \
    fonts-noto-cjk \
    fonts-noto-mono \
    fonts-deva \
    fonts-indic \
    fonts-noto-sans \
    fonts-noto-serif \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Add Mangal.ttf font for best Hindi support
RUN mkdir -p /usr/share/fonts/truetype/mangal \
    && wget -O /usr/share/fonts/truetype/mangal/Mangal.ttf https://github.com/alltools-tech/fonts/raw/main/Mangal.ttf \
    && fc-cache -fv

WORKDIR /app

COPY requirements.txt .

RUN pip uninstall -y pillow || true
RUN pip install --upgrade pip
RUN pip install --no-binary=:all: pillow
RUN pip install -r requirements.txt

COPY . /app

EXPOSE 8000

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"] 