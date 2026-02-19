# FILE: terraform/step_function.tf

resource "aws_iam_role" "step_functions_role" {
  name = "${var.project_name}-StepFunctionsRole"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "step_functions_permissions" {
  name = "StepFunctionsPermissions"
  role = aws_iam_role.step_functions_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action = "lambda:InvokeFunction",
      Effect = "Allow",
      Resource = [
        aws_lambda_function.fetch_astrology.arn,
        aws_lambda_function.architect_book.arn,
        aws_lambda_function.write_chapters.arn,
        aws_lambda_function.generate_ebook.arn,
        aws_lambda_function.generate_pdf.arn,
        aws_lambda_function.notify_lulu.arn,
        aws_lambda_function.generate_cover.arn,
        aws_lambda_function.assemble_payload.arn,
        aws_lambda_function.send_email.arn
      ]
    }]
  })
}


resource "aws_sfn_state_machine" "astrology_book_factory" {
  name     = "${var.project_name}-StateMachine"
  role_arn = aws_iam_role.step_functions_role.arn

  definition = jsonencode({
    Comment = "Generates book interior, then cover sequentially, then assembles final payload for Lulu."
    StartAt = "ProcessAllBooksInParallel"
    States = {
      ProcessAllBooksInParallel = {
        Type      = "Map",
        ItemsPath = "$.books",
        MaxConcurrency = 5,
        Parameters = {
          "order_id.$":         "$.order_id",
          "shipping_address.$": "$.shipping_address",
          "customer_details.$": "$.customer_details",
          "line_item_id.$":     "$$.Map.Item.Value.line_item_id",
          "cover_title.$":      "$$.Map.Item.Value.cover_title",
          "birth_data.$":       "$$.Map.Item.Value.birth_data",
          "focus.$":            "$$.Map.Item.Value.focus",
          "language.$":         "$$.Map.Item.Value.language",
          "requires_shipping.$": "$$.Map.Item.Value.requires_shipping"
        },
        Iterator = {
          StartAt = "FetchAstrologyData",
          States = {
            FetchAstrologyData = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.fetch_astrology.arn, "Payload.$" = "$" },
              ResultPath = "$",
              Next       = "ArchitectBook"
            },
            ArchitectBook = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.architect_book.arn, "Payload.$" = "$" },
              ResultPath = "$",
              Next       = "WriteChapters"
            },
            WriteChapters = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.write_chapters.arn, "Payload.$" = "$" },
              ResultPath = "$",
              Next       = "GenerateEbook"
            },
            GenerateEbook = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.generate_ebook.arn, "Payload.$" = "$" },
              ResultPath = "$",
              Next       = "GeneratePDF"
            },
            GeneratePDF = {
              Type           = "Task",
              Resource       = "arn:aws:states:::lambda:invoke",
              Parameters     = { "FunctionName" = aws_lambda_function.generate_pdf.arn, "Payload.$" = "$" },
              ResultPath     = "$",
              TimeoutSeconds = 900,
              Next           = "GenerateCoverImageWithPageCount"
            },
            GenerateCoverImageWithPageCount = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.generate_cover.arn, "Payload.$" = "$" },
              ResultPath = "$",
              Next       = "CombineResultsForSequential"
            },
            CombineResultsForSequential = {
              Type = "Pass",
              "Parameters": {
                "final_pdf_s3_path.$":  "$.Payload.final_pdf_s3_path",
                "cover_image_s3_url.$": "$.Payload.cover_image_s3_url",
                "ebook_s3_path.$":      "$.Payload.ebook_s3_path",
                "order_id.$":           "$.Payload.order_id",
                "line_item_id.$":       "$.Payload.line_item_id",
                "cover_title.$":        "$.Payload.cover_title",
                "shipping_address.$":   "$.Payload.shipping_address",
                "customer_details.$":   "$.Payload.customer_details",
                "requires_shipping.$":  "$.Payload.requires_shipping"
              },
              ResultPath = "$",
              Next = "BookGenerationSucceeded"
            },
            BookGenerationSucceeded = { "Type": "Succeed" }
          }
        },
        ResultPath = "$.processed_books_results",
        Catch      = [{ "ErrorEquals": ["States.All"], "Next": "OrderFailed" }],
        Next       = "AssembleFinalPayload"
      },
      AssembleFinalPayload = {
        Type       = "Task",
        Resource   = "arn:aws:states:::lambda:invoke",
        Parameters = { "FunctionName" = aws_lambda_function.assemble_payload.arn, "Payload.$" = "$" },
        ResultPath = "$",
        Next       = "NotifyLulu"
      },
      NotifyLulu = {
        Type       = "Task",
        Resource   = "arn:aws:states:::lambda:invoke",
        Parameters = { "FunctionName": aws_lambda_function.notify_lulu.arn, "Payload.$": "$.Payload" },
        ResultPath = "$.lulu_result",
        Catch      = [{ "ErrorEquals": ["States.All"], "Next": "OrderFailed" }],
        Next       = "SendEmail"
      },
      SendEmail = {
        Type       = "Task",
        Resource   = "arn:aws:states:::lambda:invoke",
        Parameters = { "FunctionName": aws_lambda_function.send_email.arn, "Payload.$": "$.Payload" },
        ResultPath = "$.email_result",
        Next       = "OrderSucceeded"
      },
      OrderSucceeded = { "Type": "Succeed" },
      OrderFailed    = { "Type": "Fail" }
    }
  })
}