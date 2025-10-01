# Use a modern, stable Linux base that has the correct system libraries
FROM python:3.11-slim-bookworm

# Set the working directory
WORKDIR /var/task

# Install all the modern system dependencies that WeasyPrint needs
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    libffi-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python requirements
COPY src/generate_pdf/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your application code and fonts
COPY src/generate_pdf/app.py .
COPY src/generate_pdf/book_pdf_exporter.py .
COPY src/generate_pdf/fonts ./fonts

# Install and configure the AWS Lambda Runtime Interface Client
RUN pip install awslambdaric
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "app.lambda_handler" ]