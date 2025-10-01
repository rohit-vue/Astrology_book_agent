# FILE: terraform/lambda_layers.tf (Final, Correct Relative Path)

resource "aws_lambda_layer_version" "shared_libraries" {
  layer_name = "SharedPythonLibraries"

  # This is a simple, relative path from the 'terraform' folder
  # up one level, and then into the '_build_artifacts' folder.
  # This will work.
  filename = "${path.module}/../_build_artifacts/shared_libraries_layer.zip"

  compatible_runtimes = ["python3.11"]

  source_code_hash = filebase64sha256("${path.module}/../_build_artifacts/shared_libraries_layer.zip")
}