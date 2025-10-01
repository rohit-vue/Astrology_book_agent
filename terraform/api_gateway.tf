#api_gateway.tf

# Create an HTTP API - simpler and cheaper than REST API
resource "aws_apigatewayv2_api" "shopify_webhook" {
  name          = "ShopifyWebhookAPI"
  protocol_type = "HTTP"
}

# Create the integration between the API and the Lambda
resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id           = aws_apigatewayv2_api.shopify_webhook.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.order_ingestion.invoke_arn
}

# Define the route: POST /shopify/webhook/order
resource "aws_apigatewayv2_route" "post_order" {
  api_id    = aws_apigatewayv2_api.shopify_webhook.id
  route_key = "POST /shopify/webhook/order"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

# Give API Gateway permission to invoke the Lambda function
resource "aws_lambda_permission" "api_gateway_permission" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.order_ingestion.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.shopify_webhook.execution_arn}/*/*"
}

# Output the API endpoint URL so you know where to send webhooks
output "webhook_url" {
  value = "${aws_apigatewayv2_api.shopify_webhook.api_endpoint}/shopify/webhook/order"
}

# 1. Create a CloudWatch Log Group to store the access logs from the API
resource "aws_cloudwatch_log_group" "api_gw_logs" {
  name              = "/aws/apigateway/${aws_apigatewayv2_api.shopify_webhook.name}"
  retention_in_days = 7 # Keep logs for 7 days to manage costs
}

# 2. Create the default deployment stage for the API. This is the CRITICAL missing piece.
# This "deploys" the API to the public URL and enables logging.
resource "aws_apigatewayv2_stage" "default" {
  api_id = aws_apigatewayv2_api.shopify_webhook.id

  # "$default" is a special name that makes this the default stage
  # accessible at the API's base invoke URL without needing a stage name in the path.
  name        = "$default"
  auto_deploy = true

  # Configure access logging and send logs to the group we created above
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw_logs.arn

    # This JSON format gives us detailed logs for debugging
    format = jsonencode({
      requestId    = "$context.requestId"
      ip           = "$context.identity.sourceIp"
      requestTime  = "$context.requestTime"
      httpMethod   = "$context.httpMethod"
      routeKey     = "$context.routeKey"
      path         = "$context.path"
      status       = "$context.status"
      errorMessage = "$context.error.message"
    })
  }
}