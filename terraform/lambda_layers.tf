# FILE: terraform/lambda_layers.tf 

resource "aws_lambda_layer_version" "shared_libraries" {
  layer_name = "SharedPythonLibraries"

  filename = "${path.module}/../_build_artifacts/shared_libraries_layer.zip"

  compatible_runtimes = ["python3.11"]

  source_code_hash = filebase64sha256("${path.module}/../_build_artifacts/shared_libraries_layer.zip")
}