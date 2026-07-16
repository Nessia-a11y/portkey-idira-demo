FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    nodejs npm curl fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure data directories exist
RUN mkdir -p data/datasheets data/internal_demos data/external_demos data/sku data/techdocs

EXPOSE 3587

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3587"]
