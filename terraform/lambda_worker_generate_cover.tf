# FILE: terraform/lambda_worker_generate_cover.tf

resource "aws_iam_role" "generate_cover_role" {
  name = "${var.project_name}-GenerateCoverRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "generate_cover_logs" {
  role       = aws_iam_role.generate_cover_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "generate_cover_permissions" {
  name = "GenerateCoverPermissions"
  role = aws_iam_role.generate_cover_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = "secretsmanager:GetSecretValue",
        Effect   = "Allow",
        Resource = aws_secretsmanager_secret.api_keys_v2.arn
      },
      {
        Action   = "ssm:GetParameter",
        Effect   = "Allow",
        Resource = "arn:aws:ssm:*:*:parameter/AstrologyBookFactory/prompts/*"
      },
      {
        Action   = ["s3:PutObject", "s3:PutObjectAcl"],
        Effect   = "Allow",
        Resource = "${aws_s3_bucket.artifacts_bucket.arn}/book-covers/*"
      }
    ]
  })
}

resource "null_resource" "build_generate_cover_package" {
  triggers = {
    app_py_hash       = filemd5("${path.module}/../src/generate_cover/app.py")
    cover_art_py_hash = filemd5("${path.module}/../src/generate_cover/cover_art.py")
    reqs_txt_hash     = filemd5("${path.module}/../src/generate_cover/requirements.txt")
    font_files_hash  = md5(join("", [for f in fileset("${path.module}/../src/generate_cover/fonts", "*") : filemd5("${path.module}/../src/generate_cover/fonts/${f}")]))
    build_recipe_rev = "lulu-cover-dimensions-v1"
  }

  provisioner "local-exec" {
    command     = <<-EOT
      docker run --rm -v "${path.module}/../:/workspace" -w /workspace python:3.11-slim sh -c "rm -rf dist/generate_cover_build && apt-get update && apt-get install -y zip fonts-noto-cjk fonts-dejavu-core && mkdir -p dist/generate_cover_build && pip install -r src/generate_cover/requirements.txt -t dist/generate_cover_build && cp -r src/generate_cover/* dist/generate_cover_build/ && find /usr/share/fonts -name 'NotoSansCJK-Regular.ttc' -print -quit | xargs -r -I{} cp -f {} dist/generate_cover_build/fonts/ && find /usr/share/fonts -name 'DejaVuSans.ttf' -print -quit | xargs -r -I{} cp -f {} dist/generate_cover_build/fonts/ && cd dist/generate_cover_build && zip -r ../generate_cover_package.zip ."
    EOT
    interpreter = ["PowerShell", "-Command"]
  }
}

data "archive_file" "generate_cover_package" {
  type        = "zip"
  source_dir  = "${path.module}/../dist/generate_cover_build"
  output_path = "${path.module}/../dist/generate_cover_package.zip"

  depends_on = [null_resource.build_generate_cover_package]
}

# Direct UpdateFunctionCode rejects zips over ~70MB; S3 deploy allows up to ~250MB.
resource "aws_s3_object" "generate_cover_package" {
  bucket = aws_s3_bucket.artifacts_bucket.id
  key    = "lambda-packages/generate_cover_package.zip"
  source = data.archive_file.generate_cover_package.output_path
  etag   = data.archive_file.generate_cover_package.output_md5
}

resource "aws_lambda_function" "generate_cover" {
  function_name = "${var.project_name}-GenerateCover"
  role          = aws_iam_role.generate_cover_role.arn

  package_type = "Zip"
  handler      = "app.lambda_handler"
  runtime      = "python3.11"
  timeout      = 300
  memory_size  = 512

  s3_bucket        = aws_s3_object.generate_cover_package.bucket
  s3_key           = aws_s3_object.generate_cover_package.key
  source_code_hash = data.archive_file.generate_cover_package.output_base64sha256

  layers = [
    aws_lambda_layer_version.shared_libraries.arn
  ]

  environment {
    variables = {
      API_KEYS_SECRET_ARN   = aws_secretsmanager_secret.api_keys_v2.arn
      ARTIFACTS_BUCKET      = aws_s3_bucket.artifacts_bucket.id
      LULU_API_BASE         = "https://api.lulu.com"
      LULU_POD_PACKAGE_ID   = "0550X0850.BW.STD.LW.060UC444.MNG"
      LULU_POD_PACKAGE_ID_HARDCOVER = "0550X0850.BW.STD.LW.060UC444.MNG"
      LULU_POD_PACKAGE_ID_PAPERBACK = "0550X0850.BW.STD.PB.060UC444.MXX"
      COVER_DYNAMIC_ENABLED   = "1"
      MODEL_COVER_IMAGE       = "gpt-image-2"
      COVER_IMAGE_SIZE        = "2560x1440"
      COVER_PROMPT_SSM_NAME   = aws_ssm_parameter.cover_image_prompt.name
    }
  }
}
