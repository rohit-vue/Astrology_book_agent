# file lambda_trigger.tf
# IAM Role for the StartExecution Lambda
resource "aws_iam_role" "start_execution_role" {
  name = "${var.project_name}-StartExecutionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Basic policy for CloudWatch Logs
resource "aws_iam_role_policy_attachment" "start_execution_logs" {
  role       = aws_iam_role.start_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Custom policy with permissions to read from SQS and start a Step Function
resource "aws_iam_role_policy" "start_execution_permissions" {
  name = "StartExecutionPermissions"
  role = aws_iam_role.start_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        # Permissions to read messages from our BookOrders queue
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
        Effect   = "Allow",
        Resource = aws_sqs_queue.book_orders.arn
      },
      {
        # Permission to start our specific Step Function
        Action   = "states:StartExecution",
        Effect   = "Allow",
        Resource = aws_sfn_state_machine.astrology_book_factory.arn
      }
    ]
  })
}

# Automatically package the Python code into a ZIP file
data "archive_file" "start_execution_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src/start_execution"
  output_path = "${path.module}/../dist/start_execution.zip"
}

# The Lambda function resource
resource "aws_lambda_function" "start_execution" {
  function_name = "${var.project_name}-StartExecution"
  role          = aws_iam_role.start_execution_role.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.11"

  filename         = data.archive_file.start_execution_zip.output_path
  source_code_hash = data.archive_file.start_execution_zip.output_base64sha256

  environment {
    variables = {
      STATE_MACHINE_ARN = aws_sfn_state_machine.astrology_book_factory.arn
    }
  }
}

# The SQS Event Source Mapping - THIS IS THE GLUE
# This tells AWS to trigger our Lambda when messages arrive in the SQS queue.
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.book_orders.arn
  function_name    = aws_lambda_function.start_execution.arn
  batch_size       = 5 # Process up to 5 messages at a time
}