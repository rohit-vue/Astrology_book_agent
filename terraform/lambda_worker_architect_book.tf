# FILE: terraform/lambda_worker_architect_book.tf 

resource "aws_iam_role" "architect_book_role" {
  name = "${var.project_name}-ArchitectBookRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "architect_book_logs" {
  role       = aws_iam_role.architect_book_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "architect_book_permissions" {
  name = "ArchitectBookPermissions"
  role = aws_iam_role.architect_book_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Action = "secretsmanager:GetSecretValue", Effect = "Allow", Resource = aws_secretsmanager_secret.api_keys_v2.arn },
      { Action = ["s3:GetObject", "s3:PutObject"], Effect = "Allow", Resource = "${aws_s3_bucket.artifacts_bucket.arn}/*" },
      { Action = "ssm:GetParameter", Effect = "Allow", Resource = "arn:aws:ssm:*:*:parameter/AstrologyBookFactory/prompts/*" }
    ]
  })
}

data "archive_file" "architect_book_code" {
  type        = "zip"
  output_path = "${path.module}/../dist/architect_book_code.zip"

  source {
    content  = file("${path.module}/../src/architect_book/app.py")
    filename = "app.py"
  }
  source {
    content  = file("${path.module}/../src/shared/structured_schemas.py")
    filename = "structured_schemas.py"
  }
}

resource "aws_lambda_function" "architect_book" {
  function_name = "${var.project_name}-ArchitectBook"
  role          = aws_iam_role.architect_book_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11" # Upgraded runtime
  timeout      = 300

  filename         = data.archive_file.architect_book_code.output_path
  source_code_hash = data.archive_file.architect_book_code.output_base64sha256

  layers = [
    aws_lambda_layer_version.shared_libraries.arn
  ]

  environment {
    variables = {
      API_KEYS_SECRET_ARN         = aws_secretsmanager_secret.api_keys_v2.arn
      ARTIFACTS_BUCKET            = aws_s3_bucket.artifacts_bucket.id
      MODEL_ARCHITECT             = "gpt-5.5"
      REASONING_EFFORT_ARCHITECT  = "high"
      TEXT_VERBOSITY_ARCHITECT    = "high"
      ARCHITECT_MAX_OUTPUT_TOKENS = "100000"
      ARCHITECT_MIN_CHAPTERS      = "1"
      ARCHITECT_MAX_CHAPTERS      = "14"
      ARCHITECT_MAX_RETRIES       = "2"
      OPENAI_TIMEOUT_SECONDS      = "120"
      OPENAI_MAX_RETRIES          = "1"
      AWS_CONNECT_TIMEOUT_SECONDS = "3"
      AWS_READ_TIMEOUT_SECONDS    = "30"
      AWS_MAX_ATTEMPTS            = "2"
    }
  }
}