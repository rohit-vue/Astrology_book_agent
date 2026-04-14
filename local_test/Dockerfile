FROM public.ecr.aws/docker/library/python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-noto \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    weasyprint==63.1 \
    jinja2 \
    requests \
    boto3 \
    openai \
    python-dotenv \
    httpx

WORKDIR /app

ENTRYPOINT ["python"]
