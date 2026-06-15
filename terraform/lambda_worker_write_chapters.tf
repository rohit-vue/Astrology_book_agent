# FILE: terraform/lambda_worker_write_chapters.tf 

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
      { Action = ["s3:GetObject", "s3:PutObject"], Effect = "Allow", Resource = "${aws_s3_bucket.artifacts_bucket.arn}/*" },
      { Action = "ssm:GetParameter", Effect = "Allow", Resource = "arn:aws:ssm:*:*:parameter/AstrologyBookFactory/prompts/*" }
    ]
  })
}

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
  memory_size  = 2048

  filename         = data.archive_file.write_chapters_code.output_path
  source_code_hash = data.archive_file.write_chapters_code.output_base64sha256

  layers = [
    aws_lambda_layer_version.shared_libraries.arn
  ]

  environment {
    variables = {
      API_KEYS_SECRET_ARN = aws_secretsmanager_secret.api_keys_v2.arn
      ARTIFACTS_BUCKET    = aws_s3_bucket.artifacts_bucket.id

      # GPT-5.5 / gpt-image-2 (Responses + Batch; align with local_test/run_local_batch_pipeline.py)
      MODEL_CONTENT                  = "gpt-5.5"
      MODEL_IMAGE                    = "gpt-image-2"
      BATCH_ENDPOINT_RESPONSES       = "/v1/responses"
      REASONING_EFFORT_CHAPTER       = "high"
      TEXT_VERBOSITY_CHAPTER         = "high"
      CHAPTER_MAX_OUTPUT_TOKENS      = "25000"
      REASONING_EFFORT_ARCHITECT     = "high"
      TEXT_VERBOSITY_ARCHITECT       = "high"
      REASONING_EFFORT_STYLE         = "medium"
      TEXT_VERBOSITY_STYLE           = "low"
      STYLE_MAX_OUTPUT_TOKENS        = "600"
      SECTION_MAX_OUTPUT_TOKENS      = "4000"
      SECTION_WORD_TARGET            = "550"
      SECTION_WORD_MIN               = "500"
      SECTION_WORD_MAX               = "600"
      IMAGE_SUMMARY_MAX_OUTPUT_TOKENS = "200"
      BATCH_POLL_INTERVAL            = "30"
      BOOK_WORD_TARGET               = "50000"
      CHAPTER_WORD_TARGET            = "7750"
      CHAPTER_WORD_MIN               = "7500"
      CHAPTER_WORD_MAX               = "8000"
      MAX_BATCH_RETRIES              = "3"
      SECTION_GENERATION_MAX_RETRIES = "2"
    }
  }
}