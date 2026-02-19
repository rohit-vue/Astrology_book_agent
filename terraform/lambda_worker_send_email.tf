resource "aws_iam_role" "send_email_role" {
  name = "${var.project_name}-SendEmailRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "send_email_logs" {
  role       = aws_iam_role.send_email_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "send_email_permissions" {
  name = "SendEmailPermissions"
  role = aws_iam_role.send_email_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = ["ses:SendEmail", "ses:SendRawEmail"],
        Effect   = "Allow",
        Resource = "*"
      },
      {
        Action   = "s3:GetObject",
        Effect   = "Allow",
        Resource = "${aws_s3_bucket.artifacts_bucket.arn}/*"
      }
    ]
  })
}

data "archive_file" "send_email_code" {
  type        = "zip"
  source_file = "${path.module}/../src/send_email/app.py"
  output_path = "${path.module}/../dist/send_email_code.zip"
}

resource "aws_lambda_function" "send_email" {
  function_name = "${var.project_name}-SendEmail"
  role          = aws_iam_role.send_email_role.arn
  package_type  = "Zip"
  handler       = "app.lambda_handler"
  runtime       = "python3.11"
  timeout       = 30

  filename         = data.archive_file.send_email_code.output_path
  source_code_hash = data.archive_file.send_email_code.output_base64sha256
}