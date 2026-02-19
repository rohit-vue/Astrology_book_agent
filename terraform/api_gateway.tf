#api_gateway.tf

resource "aws_apigatewayv2_api" "shopify_webhook" {
  name          = "ShopifyWebhookAPI"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id           = aws_apigatewayv2_api.shopify_webhook.id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.order_ingestion.invoke_arn
}

resource "aws_apigatewayv2_route" "post_order" {
  api_id    = aws_apigatewayv2_api.shopify_webhook.id
  route_key = "POST /shopify/webhook/order"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_lambda_permission" "api_gateway_permission" {
  statement_id  = "AllowAPIGatewayInvoke_V2"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.order_ingestion.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.shopify_webhook.execution_arn}/*/*"
}

output "webhook_url" {
  value = "${aws_apigatewayv2_api.shopify_webhook.api_endpoint}/shopify/webhook/order"
}

resource "aws_cloudwatch_log_group" "api_gw_logs" {
  name              = "/aws/apigateway/${aws_apigatewayv2_api.shopify_webhook.name}"
  retention_in_days = 7 # Keep logs for 7 days to manage costs
}

resource "aws_apigatewayv2_stage" "default" {
  api_id = aws_apigatewayv2_api.shopify_webhook.id

  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api_gw_logs.arn

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