# FILE: src/generate_pdf/Dockerfile (THE FINAL, CORRECTED AND COMPLETE VERSION)

# 1. Start from the modern Python 3.11 image that we know works on your machine.
FROM python:3.11-slim-bookworm

# 2. Manually set the LAMBDA_TASK_ROOT environment variable.
ENV LAMBDA_TASK_ROOT=/var/task

# 3. --- THIS IS THE FINAL FIX ---
#    Install the COMPLETE list of system libraries required by WeasyPrint.
RUN apt-get update && apt-get install -y \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev

# 4. Set the working directory.
WORKDIR ${LAMBDA_TASK_ROOT}

# 5. Install the AWS Lambda Runtime Interface Client.
RUN pip install awslambdaric

# 6. Copy and install our Python libraries (weasyprint, boto3, etc.).
COPY requirements.txt .
RUN pip install -r requirements.txt

# 7. Copy the rest of our application code.
COPY . .

# 8. Set the command that AWS Lambda will use to start the function.
ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "app.lambda_handler" ]