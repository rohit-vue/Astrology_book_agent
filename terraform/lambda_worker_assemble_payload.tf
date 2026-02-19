# FILE: terraform/lambda_worker_assemble_payload.tf

resource "aws_iam_role" "assemble_payload_role" {
  name = "${var.project_name}-AssemblePayloadRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "assemble_payload_logs" {
  role       = aws_iam_role.assemble_payload_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "archive_file" "assemble_payload_code" {
  type        = "zip"
  source_file = "${path.module}/../src/assemble_payload/app.py"
  output_path = "${path.module}/../dist/assemble_payload.zip"
}

resource "aws_lambda_function" "assemble_payload" {
  function_name = "${var.project_name}-AssemblePayload"
  role          = aws_iam_role.assemble_payload_role.arn
  package_type  = "Zip"
  handler       = "app.lambda_handler"
  runtime       = "python3.11"
  filename      = data.archive_file.assemble_payload_code.output_path
  source_code_hash = data.archive_file.assemble_payload_code.output_base64sha256
}