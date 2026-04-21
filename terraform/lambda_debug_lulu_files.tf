# FILE: terraform/lambda_debug_lulu_files.tf

resource "aws_iam_role" "debug_lulu_files_role" {
  name = "${var.project_name}-DebugLuluFilesRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "debug_lulu_files_logs" {
  role       = aws_iam_role.debug_lulu_files_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "debug_lulu_files_permissions" {
  name = "DebugLuluFilesPermissions"
  role = aws_iam_role.debug_lulu_files_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = "secretsmanager:GetSecretValue",
        Effect   = "Allow",
        Resource = aws_secretsmanager_secret.api_keys_v2.arn
      }
    ]
  })
}

data "archive_file" "debug_lulu_files_code" {
  type        = "zip"
  source_file = "${path.module}/../src/debug_lulu_files/app.py"
  output_path = "${path.module}/../dist/debug_lulu_files_code.zip"
}

resource "aws_lambda_function" "debug_lulu_files" {
  function_name    = "${var.project_name}-DebugLuluFiles"
  role             = aws_iam_role.debug_lulu_files_role.arn
  package_type     = "Zip"
  handler          = "app.lambda_handler"
  runtime          = "python3.11"
  timeout          = 120
  filename         = data.archive_file.debug_lulu_files_code.output_path
  source_code_hash = data.archive_file.debug_lulu_files_code.output_base64sha256
  layers           = [aws_lambda_layer_version.shared_libraries.arn]
  environment {
    variables = { API_KEYS_SECRET_ARN = aws_secretsmanager_secret.api_keys_v2.arn }
  }
}