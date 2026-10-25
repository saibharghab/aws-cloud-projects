variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "account-checker"
}

variable "alert_email" {
  description = "Where the daily report and failure alarms are emailed"
  type        = string
}

variable "required_tag" {
  description = "Tag every EC2 instance must have"
  type        = string
  default     = "Owner"
}

variable "report_retention_days" {
  type    = number
  default = 90
}

# EventBridge cron runs in UTC. 13:00 UTC = 8 AM Central (CDT).
variable "check_schedule" {
  type    = string
  default = "cron(0 13 * * ? *)"
}

# 02:00 UTC = 9 PM Central (CDT), every day.
variable "stop_schedule" {
  type    = string
  default = "cron(0 2 * * ? *)"
}

# 12:30 UTC = 7:30 AM Central (CDT), Monday to Friday.
variable "start_schedule" {
  type    = string
  default = "cron(30 12 ? * MON-FRI *)"
}
