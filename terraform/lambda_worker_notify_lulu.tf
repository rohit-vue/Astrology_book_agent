# FILE: terraform/lambda_worker_notify_lulu.tf

# 1. IAM Role for the NotifyLulu Lambda
resource "aws_iam_role" "notify_lulu_role" {
  name = "${var.project_name}-NotifyLuluRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

# 2. Basic policy for CloudWatch Logs
resource "aws_iam_role_policy_attachment" "notify_lulu_logs" {
  role       = aws_iam_role.notify_lulu_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# 3. Custom policy with permissions this Lambda needs
resource "aws_iam_role_policy" "notify_lulu_permissions" {
  name = "NotifyLuluPermissions"
  role = aws_iam_role.notify_lulu_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = "secretsmanager:GetSecretValue",
        Effect   = "Allow",
        Resource = aws_secretsmanager_secret.api_keys_v2.arn
      }
      # Note: We don't need S3 read access if we only pass the S3 URL to Lulu
    ]
  })
}

# 4. Create a zip of the function's code
data "archive_file" "notify_lulu_code" {
  type        = "zip"
  source_file = "${path.module}/../src/notify_lulu/app.py"
  output_path = "${path.module}/../dist/notify_lulu_code.zip"
}

# 5. Define the Lambda function resource
resource "aws_lambda_function" "notify_lulu" {
  function_name = "${var.project_name}-NotifyLulu"
  role          = aws_iam_role.notify_lulu_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11"
  timeout      = 60

  filename         = data.archive_file.notify_lulu_code.output_path
  source_code_hash = data.archive_file.notify_lulu_code.output_base64sha256

  # Attach the shared libraries layer that we already created
  layers = [
    aws_lambda_layer_version.shared_libraries.arn
  ]

  environment {
    variables = {
      API_KEYS_SECRET_ARN = aws_secretsmanager_secret.api_keys_v2.arn
    }
  }
}