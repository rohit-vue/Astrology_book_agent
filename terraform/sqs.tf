#sqs.tf
# This creates the queue to buffer incoming orders
resource "aws_sqs_queue" "book_orders_dlq" {
  name = "BookOrdersDLQ"
}

resource "aws_sqs_queue" "book_orders" {
  name = "BookOrders"

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.book_orders_dlq.arn
    maxReceiveCount     = 5
  })
}