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
        "${aws_lambda_function.fetch_astrology.arn}:*",

        aws_lambda_function.architect_book.arn,
        "${aws_lambda_function.architect_book.arn}:*",

        aws_lambda_function.write_chapters.arn,
        "${aws_lambda_function.write_chapters.arn}:*",

        aws_lambda_function.generate_ebook.arn,
        "${aws_lambda_function.generate_ebook.arn}:*",

        aws_lambda_function.generate_pdf.arn,
        "${aws_lambda_function.generate_pdf.arn}:*",

        aws_lambda_function.notify_lulu.arn,
        "${aws_lambda_function.notify_lulu.arn}:*",

        aws_lambda_function.generate_cover.arn,
        "${aws_lambda_function.generate_cover.arn}:*",

        aws_lambda_function.assemble_payload.arn,
        "${aws_lambda_function.assemble_payload.arn}:*",

        aws_lambda_function.send_email.arn,
        "${aws_lambda_function.send_email.arn}:*"
      ]
    }]
  })
}


resource "aws_sfn_state_machine" "astrology_book_factory" {
  name     = "${var.project_name}-StateMachine"
  role_arn = aws_iam_role.step_functions_role.arn

  definition = jsonencode({
    Comment = "Generates book interior and cover with a scheduled start delay."
    StartAt = "WaitUntilScheduledTime"
    States = {
      WaitUntilScheduledTime = {
        Type          = "Wait"
        TimestampPath = "$.factory_start_at"
        Next          = "ProcessAllBooksInParallel"
      },
      ProcessAllBooksInParallel = {
        Type           = "Map",
        ItemsPath      = "$.books",
        MaxConcurrency = 5,
        Parameters = {
          "order_id.$" : "$.order_id",
          "shipping_address.$" : "$.shipping_address",
          "customer_details.$" : "$.customer_details",
          "line_item_id.$" : "$$.Map.Item.Value.line_item_id",
          "cover_title.$" : "$$.Map.Item.Value.cover_title",
          "birth_data.$" : "$$.Map.Item.Value.birth_data",
          "shipping_code.$" : "$$.Map.Item.Value.shipping_code",
          "focus.$" : "$$.Map.Item.Value.focus",
          "language.$" : "$$.Map.Item.Value.language",
          "requires_shipping.$" : "$$.Map.Item.Value.requires_shipping"
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
              Parameters = { "FunctionName" = "${aws_lambda_function.write_chapters.arn}:prod", "Payload.$" = "$" },
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
              "Parameters" : {
                "final_pdf_s3_path.$" : "$.Payload.final_pdf_s3_path",
                "cover_image_s3_url.$" : "$.Payload.cover_image_s3_url",
                "ebook_s3_path.$" : "$.Payload.ebook_s3_path",
                "order_id.$" : "$.Payload.order_id",
                "line_item_id.$" : "$.Payload.line_item_id",
                "cover_title.$" : "$.Payload.cover_title",
                "birth_data.$" : "$.Payload.birth_data",
                "shipping_code.$" : "$.Payload.shipping_code",
                "shipping_address.$" : "$.Payload.shipping_address",
                "customer_details.$" : "$.Payload.customer_details",
                "requires_shipping.$" : "$.Payload.requires_shipping"
              },
              ResultPath = "$",
              Next       = "BookGenerationSucceeded"
            },
            BookGenerationSucceeded = { "Type" : "Succeed" }
          }
        },
        ResultPath = "$.processed_books_results",
        Catch      = [{ "ErrorEquals" : ["States.All"], "Next" : "OrderFailed" }],
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
        Parameters = { "FunctionName" : aws_lambda_function.notify_lulu.arn, "Payload.$" : "$.Payload" },
        ResultPath = "$.lulu_result",
        Catch      = [{ "ErrorEquals" : ["States.All"], "Next" : "OrderFailed" }],
        Next       = "SendEmail"
      },
      SendEmail = {
        Type       = "Task",
        Resource   = "arn:aws:states:::lambda:invoke",
        Parameters = { "FunctionName" : aws_lambda_function.send_email.arn, "Payload.$" : "$.Payload" },
        ResultPath = "$.email_result",
        Next       = "OrderSucceeded"
      },
      OrderSucceeded = { "Type" : "Succeed" },
      OrderFailed    = { "Type" : "Fail" }
    }
  })
}

locals {
  wc_common_payload_refs = {
    "order_id.$"               = "$.Payload.order_id"
    "line_item_id.$"           = "$.Payload.line_item_id"
    "focus.$"                  = "$.Payload.focus"
    "language.$"               = "$.Payload.language"
    "astrology_json_s3_path.$" = "$.Payload.astrology_json_s3_path"
    "book_structure_s3_path.$" = "$.Payload.book_structure_s3_path"
    "shipping_address.$"       = "$.Payload.shipping_address"
    "customer_details.$"       = "$.Payload.customer_details"
    "cover_title.$"            = "$.Payload.cover_title"
    "birth_data.$"             = "$.Payload.birth_data"
    "shipping_code.$"          = "$.Payload.shipping_code"
    "requires_shipping.$"      = "$.Payload.requires_shipping"
  }
}

# v2: WriteChapters split into parallel OpenAI Batch tracks + finalize (no long Lambda polling).
resource "aws_sfn_state_machine" "astrology_book_factory_v2" {
  name     = "${var.project_name}-StateMachine-v2"
  role_arn = aws_iam_role.step_functions_role.arn

  definition = jsonencode({
    Comment = "Generates book interior and cover with a scheduled start delay (WriteChapters v2: parallel batch tracks)."
    StartAt = "WaitUntilScheduledTime"
    States = {
      WaitUntilScheduledTime = {
        Type          = "Wait"
        TimestampPath = "$.factory_start_at"
        Next          = "ProcessAllBooksInParallel"
      },
      ProcessAllBooksInParallel = {
        Type           = "Map"
        ItemsPath      = "$.books"
        MaxConcurrency = 5
        Parameters = {
          "order_id.$"          = "$.order_id",
          "shipping_address.$"  = "$.shipping_address",
          "customer_details.$"  = "$.customer_details",
          "line_item_id.$"      = "$$.Map.Item.Value.line_item_id",
          "cover_title.$"       = "$$.Map.Item.Value.cover_title",
          "birth_data.$"        = "$$.Map.Item.Value.birth_data",
          "shipping_code.$"     = "$$.Map.Item.Value.shipping_code",
          "focus.$"             = "$$.Map.Item.Value.focus",
          "language.$"          = "$$.Map.Item.Value.language",
          "requires_shipping.$" = "$$.Map.Item.Value.requires_shipping"
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
              Next       = "WriteChaptersParallel"
            },
            WriteChaptersParallel = {
              Type = "Parallel",
              Branches = [
                {
                  StartAt = "SubmitTextBatch",
                  States = {
                    SubmitTextBatch = {
                      Type     = "Task",
                      Resource = "arn:aws:states:::lambda:invoke",
                      Parameters = merge({
                        FunctionName = aws_lambda_function.write_chapters.arn
                        Payload = merge({
                          operation = "submit_text_batch"
                        }, local.wc_common_payload_refs)
                      }),
                      ResultPath = "$",
                      Next       = "WaitTextPoll"
                    },
                    WaitTextPoll = {
                      Type    = "Wait",
                      Seconds = 45,
                      Next    = "CheckTextBatch"
                    },
                    CheckTextBatch = {
                      Type     = "Task",
                      Resource = "arn:aws:states:::lambda:invoke",
                      Parameters = merge({
                        FunctionName = aws_lambda_function.write_chapters.arn
                        Payload = merge({
                          operation = "check_text_batch"
                        }, local.wc_common_payload_refs)
                      }),
                      ResultPath = "$",
                      Next       = "TextBatchChoice"
                    },
                    TextBatchChoice = {
                      Type = "Choice",
                      Choices = [
                        {
                          Variable      = "$.Payload.wc_text_batch_terminal",
                          BooleanEquals = false,
                          Next          = "WaitTextPoll"
                        },
                        {
                          And = [
                            { Variable = "$.Payload.wc_text_batch_terminal", BooleanEquals = true },
                            { Variable = "$.Payload.wc_text_batch_status", StringEquals = "completed" }
                          ],
                          Next = "CollectTextResults"
                        },
                        {
                          And = [
                            { Variable = "$.Payload.wc_text_batch_terminal", BooleanEquals = true },
                            { Variable = "$.Payload.wc_text_batch_status", StringEquals = "expired" }
                          ],
                          Next = "CollectTextResults"
                        }
                      ],
                      Default = "WriteChaptersTextFailed"
                    },
                    CollectTextResults = {
                      Type     = "Task",
                      Resource = "arn:aws:states:::lambda:invoke",
                      Parameters = merge({
                        FunctionName = aws_lambda_function.write_chapters.arn
                        Payload = merge({
                          operation = "collect_text_results"
                        }, local.wc_common_payload_refs)
                      }),
                      ResultPath = "$",
                      Next       = "CollectTextRoute"
                    },
                    CollectTextRoute = {
                      Type = "Choice",
                      Choices = [
                        {
                          Variable      = "$.Payload.wc_text_collect_complete",
                          BooleanEquals = true,
                          Next          = "TextTrackDone"
                        },
                        {
                          Variable      = "$.Payload.wc_text_track_need_wait",
                          BooleanEquals = true,
                          Next          = "WaitTextPoll"
                        }
                      ],
                      Default = "WriteChaptersTextFailed"
                    },
                    TextTrackDone = {
                      Type = "Pass",
                      End  = true
                    },
                    WriteChaptersTextFailed = {
                      Type  = "Fail",
                      Error = "WriteChaptersTextTrackFailed",
                      Cause = "Text batch did not complete successfully."
                    }
                  }
                },
                {
                  StartAt = "SubmitImageBatch",
                  States = {
                    SubmitImageBatch = {
                      Type     = "Task",
                      Resource = "arn:aws:states:::lambda:invoke",
                      Parameters = merge({
                        FunctionName = aws_lambda_function.write_chapters.arn
                        Payload = merge({
                          operation = "submit_image_batch"
                        }, local.wc_common_payload_refs)
                      }),
                      ResultPath = "$",
                      Next       = "WaitImagePoll"
                    },
                    WaitImagePoll = {
                      Type    = "Wait",
                      Seconds = 45,
                      Next    = "CheckImageBatch"
                    },
                    CheckImageBatch = {
                      Type     = "Task",
                      Resource = "arn:aws:states:::lambda:invoke",
                      Parameters = merge({
                        FunctionName = aws_lambda_function.write_chapters.arn
                        Payload = merge({
                          operation = "check_image_batch"
                        }, local.wc_common_payload_refs)
                      }),
                      ResultPath = "$",
                      Next       = "ImageBatchChoice"
                    },
                    ImageBatchChoice = {
                      Type = "Choice",
                      Choices = [
                        {
                          Variable      = "$.Payload.wc_image_batch_terminal",
                          BooleanEquals = false,
                          Next          = "WaitImagePoll"
                        },
                        {
                          And = [
                            { Variable = "$.Payload.wc_image_batch_terminal", BooleanEquals = true },
                            { Variable = "$.Payload.wc_image_batch_status", StringEquals = "completed" }
                          ],
                          Next = "CollectImageResults"
                        },
                        {
                          And = [
                            { Variable = "$.Payload.wc_image_batch_terminal", BooleanEquals = true },
                            { Variable = "$.Payload.wc_image_batch_status", StringEquals = "expired" }
                          ],
                          Next = "CollectImageResults"
                        }
                      ],
                      Default = "ImageTrackSkipped"
                    },
                    CollectImageResults = {
                      Type     = "Task",
                      Resource = "arn:aws:states:::lambda:invoke",
                      Parameters = merge({
                        FunctionName = aws_lambda_function.write_chapters.arn
                        Payload = merge({
                          operation = "collect_image_results"
                        }, local.wc_common_payload_refs)
                      }),
                      ResultPath = "$",
                      Next       = "CollectImageRoute"
                    },
                    CollectImageRoute = {
                      Type = "Choice",
                      Choices = [
                        {
                          Variable      = "$.Payload.wc_image_collect_complete",
                          BooleanEquals = true,
                          Next          = "ImageTrackDone"
                        },
                        {
                          Variable      = "$.Payload.wc_image_track_need_wait",
                          BooleanEquals = true,
                          Next          = "WaitImagePoll"
                        }
                      ],
                      Default = "WriteChaptersImageFailed"
                    },
                    ImageTrackDone = {
                      Type = "Pass",
                      End  = true
                    },
                    ImageTrackSkipped = {
                      Type    = "Pass",
                      Comment = "Image batch failed before completion (e.g. cancelled); finalize without images.",
                      End     = true
                    },
                    WriteChaptersImageFailed = {
                      Type  = "Fail",
                      Error = "WriteChaptersImageTrackFailed",
                      Cause = "Image batch collect did not finish successfully."
                    }
                  }
                }
              ],
              Next = "NormalizeWriteChaptersParallel"
            },
            NormalizeWriteChaptersParallel = {
              Type = "Pass",
              Parameters = {
                "textTrack.$"  = "$[0]"
                "imageTrack.$" = "$[1]"
              },
              Next = "WriteChaptersFinalize"
            },
            WriteChaptersFinalize = {
              Type     = "Task",
              Resource = "arn:aws:states:::lambda:invoke",
              Parameters = {
                FunctionName = aws_lambda_function.write_chapters.arn
                Payload = {
                  operation                  = "finalize"
                  "order_id.$"               = "$.textTrack.Payload.order_id"
                  "line_item_id.$"           = "$.textTrack.Payload.line_item_id"
                  "focus.$"                  = "$.textTrack.Payload.focus"
                  "language.$"               = "$.textTrack.Payload.language"
                  "astrology_json_s3_path.$" = "$.textTrack.Payload.astrology_json_s3_path"
                  "book_structure_s3_path.$" = "$.textTrack.Payload.book_structure_s3_path"
                }
              },
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
              Parameters = { "FunctionName" = "${aws_lambda_function.generate_cover.arn}:prod", "Payload.$" = "$" },
              ResultPath = "$",
              Next       = "CombineResultsForSequential"
            },
            CombineResultsForSequential = {
              Type = "Pass",
              Parameters = {
                "final_pdf_s3_path.$"  = "$.Payload.final_pdf_s3_path",
                "cover_image_s3_url.$" = "$.Payload.cover_image_s3_url",
                "ebook_s3_path.$"      = "$.Payload.ebook_s3_path",
                "order_id.$"           = "$.Payload.order_id",
                "line_item_id.$"       = "$.Payload.line_item_id",
                "cover_title.$"        = "$.Payload.cover_title",
                "birth_data.$"         = "$.Payload.birth_data",
                "shipping_code.$"      = "$.Payload.shipping_code",
                "shipping_address.$"   = "$.Payload.shipping_address",
                "customer_details.$"   = "$.Payload.customer_details",
                "requires_shipping.$"  = "$.Payload.requires_shipping"
              },
              ResultPath = "$",
              Next       = "BookGenerationSucceeded"
            },
            BookGenerationSucceeded = { Type = "Succeed" }
          }
        },
        ResultPath = "$.processed_books_results",
        Catch      = [{ ErrorEquals = ["States.All"], Next = "OrderFailed" }],
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
        Parameters = { "FunctionName" = aws_lambda_function.notify_lulu.arn, "Payload.$" = "$.Payload" },
        ResultPath = "$.lulu_result",
        Catch      = [{ ErrorEquals = ["States.All"], Next = "OrderFailed" }],
        Next       = "SendEmail"
      },
      SendEmail = {
        Type       = "Task",
        Resource   = "arn:aws:states:::lambda:invoke",
        Parameters = { "FunctionName" = aws_lambda_function.send_email.arn, "Payload.$" = "$.Payload" },
        ResultPath = "$.email_result",
        Next       = "OrderSucceeded"
      },
      OrderSucceeded = { Type = "Succeed" },
      OrderFailed    = { Type = "Fail" }
    }
  })
}