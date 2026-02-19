# FILE: src/order_ingestion/Dockerfile (Final, Guaranteed Version)

FROM python:3.11-slim-buster

WORKDIR ${LAMBDA_TASK_ROOT}

RUN pip install awslambdaric

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .

CMD [ "app.lambda_handler" ]