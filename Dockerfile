# Use the official AWS Lambda Python 3.11 base image
FROM public.ecr.aws/lambda/python:3.11

COPY src/notify_lulu/app.py ${LAMBDA_TASK_ROOT}/

CMD [ "app.lambda_handler" ]