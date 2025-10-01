# FILE: terraform/lambda_ingestion.tf (Final Zip + Layer Version)

# This creates a tiny zip file containing ONLY your app.py code
data "archive_file" "order_ingestion_code" {
  type        = "zip"
  source_file = "${path.module}/../src/order_ingestion/app.py"
  output_path = "${path.module}/../dist/order_ingestion_code.zip"
}

resource "aws_lambda_function" "order_ingestion" {
  function_name = "${var.project_name}-OrderIngestion"
  role          = aws_iam_role.order_ingestion_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11" # Using the new Python version

  filename         = data.archive_file.order_ingestion_code.output_path
  source_code_hash = data.archive_file.order_ingestion_code.output_base64sha256

  # This is the magic line that attaches the "libraries backpack"
  layers = [
    aws_lambda_layer_version.shared_libraries.arn
  ]

  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      ORDERS_TABLE_NAME     = aws_dynamodb_table.orders_table.name
      BOOK_ORDERS_QUEUE_URL = aws_sqs_queue.book_orders.id
      RAW_PAYLOADS_BUCKET   = aws_s3_bucket.artifacts_bucket.id
      API_KEYS_SECRET_ARN   = aws_secretsmanager_secret.api_keys_v2.arn
    }
  }
}