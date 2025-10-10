# FILE: terraform/step_function.tf (Final Version with Lulu Integration)

# -----------------------------------------------------------------------------
# IAM ROLE FOR THE STEP FUNCTION 
# -----------------------------------------------------------------------------

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
        aws_lambda_function.generate_pdf.arn,
        aws_lambda_function.notify_lulu.arn, # <-- Added Lulu permission
      ]
    }]
  })
}

# -----------------------------------------------------------------------------
# THE STEP FUNCTIONS STATE MACHINE (FINAL VERSION) 
# -----------------------------------------------------------------------------

resource "aws_sfn_state_machine" "astrology_book_factory" {
  name     = "${var.project_name}-StateMachine"
  role_arn = aws_iam_role.step_functions_role.arn

  definition = jsonencode({
    Comment = "Processes a Shopify order, creates books in parallel, and submits to Lulu."
    StartAt = "ProcessAllBooksInParallel"
    States = {
      ProcessAllBooksInParallel = {
        Type      = "Map",
        ItemsPath = "$.books",
        Parameters = {
          "order_id.$"         = "$.order_id",
          "line_item_id.$"     = "$$.Map.Item.Value.line_item_id",
          "cover_title.$"      = "$$.Map.Item.Value.cover_title",
          "birth_data.$"       = "$$.Map.Item.Value.birth_data",
          "shipping_address.$" = "$.shipping_address"
        },
        Iterator = {
          StartAt = "FetchAstrologyData",
          States = {
            FetchAstrologyData = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.fetch_astrology.arn, "Payload.$" = "$" },
              ResultPath = "$", # This simple path passes the full result to the next step
              Catch      = [{ "ErrorEquals" : ["States.All"], "ResultPath" : "$.error", "Next" : "BookGenerationFailed" }],
              Next       = "ArchitectBook"
            },
            ArchitectBook = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.architect_book.arn, "Payload.$" = "$" },
              ResultPath = "$", # This simple path passes the full result to the next step
              Catch      = [{ "ErrorEquals" : ["States.All"], "ResultPath" : "$.error", "Next" : "BookGenerationFailed" }],
              Next       = "WriteChapters"
            },
            WriteChapters = {
              Type       = "Task",
              Resource   = "arn:aws:states:::lambda:invoke",
              Parameters = { "FunctionName" = aws_lambda_function.write_chapters.arn, "Payload.$" = "$" },
              ResultPath = "$", # This simple path passes the full result to the next step
              Catch      = [{ "ErrorEquals" : ["States.All"], "ResultPath" : "$.error", "Next" : "BookGenerationFailed" }],
              Next       = "GeneratePDF"
            },
            GeneratePDF = {
              Type           = "Task",
              Resource       = "arn:aws:states:::lambda:invoke",
              Parameters     = { "FunctionName" = aws_lambda_function.generate_pdf.arn, "Payload.$" = "$" },
              ResultPath     = "$", # This simple path passes the full result to the next step
              TimeoutSeconds = 840,
              Catch          = [{ "ErrorEquals" : ["States.All"], "ResultPath" : "$.error", "Next" : "BookGenerationFailed" }],
              Next           = "BookGenerationSucceeded"
            },
            BookGenerationSucceeded = { "Type" : "Succeed" },
            BookGenerationFailed    = { "Type" : "Fail" }
          }
        },
        ResultPath = "$.processed_books_results",
        Catch      = [{ "ErrorEquals" : ["States.All"], "Next" : "OrderFailed" }],
        Next       = "NotifyLulu"
      },

      NotifyLulu = {
        Type     = "Task",
        Resource = "arn:aws:states:::lambda:invoke",
        Parameters = {
          FunctionName = "${aws_lambda_function.notify_lulu.arn}",
          "Payload.$"  = "$"
        },
        ResultPath = "$.lulu_submission_result",
        Catch = [{
          ErrorEquals = ["States.All"],
          Next        = "OrderFailed"
        }],
        Next = "OrderSucceeded"
      },

      OrderSucceeded = { Type = "Succeed" },

      OrderFailed = {
        Type  = "Fail",
        Cause = "The order processing failed. See execution history for details.",
        Error = "One or more steps in the workflow failed."
      }
    }
  })
}