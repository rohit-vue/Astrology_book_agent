#sqs.tf

resource "aws_sqs_queue" "book_orders_dlq" {
  name = "Q1_ASTRO_REQUEST_DLQ"
}

resource "aws_sqs_queue" "book_orders" {
  name                       = "Q1_ASTRO_REQUEST"
  visibility_timeout_seconds = 180

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.book_orders_dlq.arn
    maxReceiveCount     = 5
  })
}