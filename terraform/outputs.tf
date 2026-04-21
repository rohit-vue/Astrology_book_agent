output "state_machine_arn_v1" {
  description = "Current production state machine ARN"
  value       = aws_sfn_state_machine.astrology_book_factory.arn
}

output "state_machine_arn_v2" {
  description = "Duplicate state machine ARN for v2 testing"
  value       = aws_sfn_state_machine.astrology_book_factory_v2.arn
}
