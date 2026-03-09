# IAM Role
resource "aws_iam_role" "generate_ebook_role" {
  name = "${var.project_name}-GenerateEbookRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "generate_ebook_logs" {
  role       = aws_iam_role.generate_ebook_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "generate_ebook_permissions" {
  name = "GenerateEbookPermissions"
  role = aws_iam_role.generate_ebook_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = ["s3:GetObject", "s3:PutObject"],
        Effect   = "Allow",
        Resource = "${aws_s3_bucket.artifacts_bucket.arn}/*"
      }
    ]
  })
}

resource "null_resource" "build_generate_ebook_package" {
  triggers = {
    app_py_hash        = filemd5("${path.module}/../src/generate_ebook/app.py")
    reqs_txt_hash      = filemd5("${path.module}/../src/generate_ebook/requirements.txt")
    build_dir_present  = fileexists("${path.module}/../dist/generate_ebook_build/app.py") ? "present" : "missing"
    package_zip_exists = fileexists("${path.module}/../dist/generate_ebook_package.zip") ? "present" : "missing"
  }

  provisioner "local-exec" {
    command     = <<-EOT
      docker run --rm -v "${path.module}/../:/workspace" -w /workspace public.ecr.aws/sam/build-python3.11 bash -c "rm -rf dist/generate_ebook_build && mkdir -p dist/generate_ebook_build && pip install -r src/generate_ebook/requirements.txt -t dist/generate_ebook_build && cp -r src/generate_ebook/* dist/generate_ebook_build/ && cd dist/generate_ebook_build && zip -r ../generate_ebook_package.zip ."
    EOT
    interpreter = ["PowerShell", "-Command"]
  }
}

data "archive_file" "generate_ebook_package" {
  type        = "zip"
  source_dir  = "${path.module}/../dist/generate_ebook_build"
  output_path = "${path.module}/../dist/generate_ebook_package.zip"
  depends_on  = [null_resource.build_generate_ebook_package]
}

resource "aws_lambda_function" "generate_ebook" {
  function_name = "${var.project_name}-GenerateEbook"
  role          = aws_iam_role.generate_ebook_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11"
  timeout      = 300
  memory_size  = 512

  filename         = data.archive_file.generate_ebook_package.output_path
  source_code_hash = data.archive_file.generate_ebook_package.output_base64sha256

  environment {
    variables = {
      ARTIFACTS_BUCKET = aws_s3_bucket.artifacts_bucket.id
    }
  }
}
