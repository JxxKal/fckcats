FROM python:3.12-slim

# poppler-utils  -> pdftotext, extrahiert die Zeitnachweis-PDFs
# libxmlsec1-dev -> Signaturpruefung der SAML-Assertions (python3-saml)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        pkg-config \
        libxml2-dev \
        libxmlsec1-dev \
        libxmlsec1-openssl \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
