# FILE: src/order_ingestion/Dockerfile (Final, Guaranteed Version)

# 1. Start from a standard, basic Python 3.11 image. This is universally compatible.
FROM python:3.11-slim-buster

# 2. Set the working directory inside the container.
WORKDIR ${LAMBDA_TASK_ROOT}

# 3. Install the AWS Lambda Runtime Interface Client (RIC).
#    This is the software that lets our Python code talk to the Lambda service.
RUN pip install awslambdaric

# 4. Copy the requirements file and install our application's dependencies.
COPY requirements.txt .
RUN pip install -r requirements.txt

# 5. Copy our function's handler code.
COPY app.py .

# 6. Set the command to run when the container starts.
CMD [ "app.lambda_handler" ]