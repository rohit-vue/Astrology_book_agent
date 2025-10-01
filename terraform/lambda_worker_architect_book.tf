# FILE: terraform/lambda_worker_architect_book.tf (Final Zip + Layer Version)

# The IAM role and policies do not need to change.
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
      { Action = ["s3:GetObject", "s3:PutObject"], Effect = "Allow", Resource = "${aws_s3_bucket.artifacts_bucket.arn}/*" }
    ]
  })
}

# --- START OF CHANGES ---
data "archive_file" "architect_book_code" {
  type        = "zip"
  source_file = "${path.module}/../src/architect_book/app.py"
  output_path = "${path.module}/../dist/architect_book_code.zip"
}

resource "aws_lambda_function" "architect_book" {
  function_name = "${var.project_name}-ArchitectBook"
  role          = aws_iam_role.architect_book_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11" # Upgraded runtime
  timeout      = 120

  filename         = data.archive_file.architect_book_code.output_path
  source_code_hash = data.archive_file.architect_book_code.output_base64sha256

  layers = [
    aws_lambda_layer_version.shared_libraries.arn
  ]

  environment {
    variables = {
      API_KEYS_SECRET_ARN = aws_secretsmanager_secret.api_keys_v2.arn
      ARTIFACTS_BUCKET    = aws_s3_bucket.artifacts_bucket.id
    }
  }
}
# --- END OF CHANGES ---