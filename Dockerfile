# Use the official AWS Lambda Python 3.11 base image
FROM public.ecr.aws/lambda/python:3.11

# Copy your function's Python code into the container
COPY src/notify_lulu/app.py ${LAMBDA_TASK_ROOT}/

# The requests and boto3 libraries are already included in this base image,
# so we don't need a requirements.txt unless you have other dependencies.

# Set the command to the Lambda handler
CMD [ "app.lambda_handler" ]