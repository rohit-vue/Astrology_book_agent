# terraform/codebuild.tf

# IAM Role for CodeBuild
resource "aws_iam_role" "codebuild_role" {
  name = "${var.project_name}-CodeBuildRole"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [{ Effect = "Allow", Principal = { Service = "codebuild.amazonaws.com" }, Action = "sts:AssumeRole" }],
  })
}

resource "aws_iam_role_policy" "codebuild_permissions" {
  name = "CodeBuildPermissions"
  role = aws_iam_role.codebuild_role.id
  policy = jsonencode({
    Version   = "2012-10-17",
    Statement = [
      { 
        Effect = "Allow", 
        Action = ["ecr:GetAuthorizationToken", "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], 
        Resource = "*" 
      },
      { 
        Effect = "Allow", 
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], 
        Resource = "*" 
      }
    ],
  })
}

resource "aws_codebuild_project" "pdf_generator_build" {
  name          = "${var.project_name}-pdf-generator-build"
  description   = "Builds the PDF generator Docker image"
  service_role  = aws_iam_role.codebuild_role.arn
  build_timeout = "20"

  artifacts { type = "NO_ARTIFACTS" }

  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:5.0" 
    type            = "LINUX_CONTAINER"
    privileged_mode = true
    
    environment_variable {
      name  = "AWS_ACCOUNT_ID"
      value = data.aws_caller_identity.current.account_id
    }
    environment_variable {
      name  = "AWS_REGION"
      value = var.aws_region
    }
    environment_variable {
      name  = "ECR_REPOSITORY_NAME"
      value = aws_ecr_repository.pdf_generator_repo.name
    }
    environment_variable {
      name  = "IMAGE_TAG"
      value = "latest"
    }
  }

  source {
    type      = "GITHUB"
    location  = "https://github.com/museaskew/musesaskew-Astrology_book_agent.git" 
    buildspec = "buildspec.yml" 
  }
}