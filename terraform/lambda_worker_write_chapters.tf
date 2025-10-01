# FILE: terraform/lambda_worker_write_chapters.tf (Final Zip + Layer Version)

# The IAM role and policies do not need to change.
resource "aws_iam_role" "write_chapters_role" {
  name = "${var.project_name}-WriteChaptersRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "write_chapters_logs" {
  role       = aws_iam_role.write_chapters_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "write_chapters_permissions" {
  name = "WriteChaptersPermissions"
  role = aws_iam_role.write_chapters_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Action = "secretsmanager:GetSecretValue", Effect = "Allow", Resource = aws_secretsmanager_secret.api_keys_v2.arn },
      { Action = ["s3:GetObject", "s3:PutObject"], Effect = "Allow", Resource = "${aws_s3_bucket.artifacts_bucket.arn}/*" }
    ]
  })
}

# --- START OF CHANGES ---
data "archive_file" "write_chapters_code" {
  type        = "zip"
  source_file = "${path.module}/../src/write_chapters/app.py"
  output_path = "${path.module}/../dist/write_chapters_code.zip"
}

resource "aws_lambda_function" "write_chapters" {
  function_name = "${var.project_name}-WriteChapters"
  role          = aws_iam_role.write_chapters_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11" # Upgraded runtime
  timeout      = 900
  memory_size  = 512

  filename         = data.archive_file.write_chapters_code.output_path
  source_code_hash = data.archive_file.write_chapters_code.output_base64sha256

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