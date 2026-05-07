output "state_machine_arn_v1" {
  description = "Legacy state machine (monolithic WriteChapters path)"
  value       = aws_sfn_state_machine.astrology_book_factory.arn
}

output "state_machine_arn_v2" {
  description = "Primary production state machine (parallel batch WriteChapters)"
  value       = aws_sfn_state_machine.astrology_book_factory_v2.arn
}

output "state_machine_arn" {
  description = "Same as state_machine_arn_v2 — default pipeline started by StartExecution"
  value       = aws_sfn_state_machine.astrology_book_factory_v2.arn
}
