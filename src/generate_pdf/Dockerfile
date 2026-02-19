FROM public.ecr.aws/docker/library/python:3.11-slim-bookworm

ENV LAMBDA_TASK_ROOT=/var/task

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

WORKDIR ${LAMBDA_TASK_ROOT}

RUN pip install awslambdaric

COPY src/generate_pdf/requirements.txt .
RUN pip install -r requirements.txt

COPY src/generate_pdf/assets/ assets/

COPY src/generate_pdf/fonts/ fonts/

COPY src/generate_pdf/. .

ENTRYPOINT [ "/usr/local/bin/python", "-m", "awslambdaric" ]
CMD [ "app.lambda_handler" ]