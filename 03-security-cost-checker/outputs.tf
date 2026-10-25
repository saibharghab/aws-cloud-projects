output "report_bucket" {
  value = aws_s3_bucket.reports.bucket
}

output "checker_function" {
  value = aws_lambda_function.checker.function_name
}

output "scheduler_function" {
  value = aws_lambda_function.scheduler.function_name
}
