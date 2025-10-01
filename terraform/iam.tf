# file iam.tf
# Role specifically for the Order Ingestion Lambda
resource "aws_iam_role" "order_ingestion_role" {
  name = "${var.project_name}-OrderIngestionRole"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action    = "sts:AssumeRole",
      Effect    = "Allow",
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Attaches the basic policy for CloudWatch Logs
resource "aws_iam_role_policy_attachment" "ingestion_lambda_logs" {
  role       = aws_iam_role.order_ingestion_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Custom policy with the exact permissions this Lambda needs
resource "aws_iam_role_policy" "order_ingestion_permissions" {
  name = "OrderIngestionPermissions"
  role = aws_iam_role.order_ingestion_role.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Action   = "sqs:SendMessage",
        Effect   = "Allow",
        Resource = aws_sqs_queue.book_orders.arn
      },
      {
        Action   = "dynamodb:PutItem",
        Effect   = "Allow",
        Resource = aws_dynamodb_table.orders_table.arn
      },
      {
        # To store the raw Shopify payload
        Action   = "s3:PutObject",
        Effect   = "Allow",
        Resource = "${aws_s3_bucket.artifacts_bucket.arn}/raw-payloads/*"
      },
      {
        # To fetch the Shopify signing secret
        Action   = "secretsmanager:GetSecretValue",
        Effect   = "Allow",
        Resource = aws_secretsmanager_secret.api_keys_v2.arn
      }
    ]
  })
}
