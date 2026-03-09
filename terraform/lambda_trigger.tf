# file lambda_trigger.tf
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

resource "aws_iam_role_policy_attachment" "start_execution_logs" {
  role       = aws_iam_role.start_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "start_execution_permissions" {
  name = "StartExecutionPermissions"
  role = aws_iam_role.start_execution_role.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:SendMessage"],
        Effect   = "Allow",
        Resource = aws_sqs_queue.book_orders.arn
      },
      {
        Action   = "states:StartExecution",
        Effect   = "Allow",
        Resource = aws_sfn_state_machine.astrology_book_factory.arn
      }
    ]
  })
}

data "archive_file" "start_execution_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../src/start_execution"
  output_path = "${path.module}/../dist/start_execution.zip"
}

resource "aws_lambda_function" "start_execution" {
  function_name = "${var.project_name}-StartExecution"
  role          = aws_iam_role.start_execution_role.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.11"

  filename         = data.archive_file.start_execution_zip.output_path
  source_code_hash = data.archive_file.start_execution_zip.output_base64sha256

  environment {
    variables = {
      STATE_MACHINE_ARN     = aws_sfn_state_machine.astrology_book_factory.arn
      BOOK_ORDERS_QUEUE_URL = aws_sqs_queue.book_orders.id
    }
  }
}

resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.book_orders.arn
  function_name    = aws_lambda_function.start_execution.arn
  batch_size       = 5 # Process up to 5 messages at a time
}