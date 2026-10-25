output "app_url" {
  description = "Open this in a browser"
  value       = "http://${aws_lb.main.dns_name}"
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}

output "asg_name" {
  value = aws_autoscaling_group.app.name
}
