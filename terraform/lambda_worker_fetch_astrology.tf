# FILE: terraform/lambda_worker_fetch_astrology.tf (Final Zip + Layer Version)

# The IAM role and policies do not need to change.
resource "aws_iam_role" "fetch_astrology_role" {
  name = "${var.project_name}-FetchAstrologyRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "fetch_astrology_logs" {
  role       = aws_iam_role.fetch_astrology_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_iam_role_policy" "fetch_astrology_permissions" {
  name = "FetchAstrologyPermissions"
  role = aws_iam_role.fetch_astrology_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      { Action = "secretsmanager:GetSecretValue", Effect = "Allow", Resource = aws_secretsmanager_secret.api_keys_v2.arn },
      { Action = ["s3:GetObject", "s3:PutObject"], Effect = "Allow", Resource = "${aws_s3_bucket.artifacts_bucket.arn}/*" }
    ]
  })
}

# --- START OF CHANGES ---
# This data source now creates a simple zip of ONLY your app.py
data "archive_file" "fetch_astrology_code" {
  type        = "zip"
  source_file = "${path.module}/../src/fetch_astrology/app.py"
  output_path = "${path.module}/../dist/fetch_astrology_code.zip"
}

resource "aws_lambda_function" "fetch_astrology" {
  function_name = "${var.project_name}-FetchAstrology"
  role          = aws_iam_role.fetch_astrology_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11" # Upgraded runtime

  filename         = data.archive_file.fetch_astrology_code.output_path
  source_code_hash = data.archive_file.fetch_astrology_code.output_base64sha256

  # Attach the shared libraries "backpack"
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