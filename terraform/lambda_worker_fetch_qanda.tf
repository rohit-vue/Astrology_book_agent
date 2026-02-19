# FILE: terraform/lambda_worker_fetch_qanda.tf

resource "aws_iam_role" "fetch_qanda_role" {
  name = "${var.project_name}-FetchQandARole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "fetch_qanda_logs" {
  role       = aws_iam_role.fetch_qanda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "fetch_qanda_permissions" {
  name = "FetchQandAPermissions"
  role = aws_iam_role.fetch_qanda_role.name
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = "s3:GetObject",
        Effect   = "Allow",
        Resource = "${aws_s3_bucket.artifacts_bucket.arn}/QandA.txt"
      }
    ]
  })
}

data "archive_file" "fetch_qanda_code" {
  type        = "zip"
  source_file = "${path.module}/../src/fetch_qanda_data/app.py"
  output_path = "${path.module}/../dist/fetch_qanda_code.zip"
}

resource "aws_lambda_function" "fetch_qanda" {
  function_name = "${var.project_name}-FetchQandAData"
  role          = aws_iam_role.fetch_qanda_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11"
  timeout      = 30

  filename         = data.archive_file.fetch_qanda_code.output_path
  source_code_hash = data.archive_file.fetch_qanda_code.output_base64sha256

  environment {
    variables = {
      QANDA_S3_URI = "s3://${aws_s3_bucket.artifacts_bucket.id}/QandA.txt"
    }
  }
}