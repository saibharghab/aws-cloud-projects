variable "region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource"
  type        = string
  default     = "ha-web-app"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "instance_type" {
  description = "EC2 instance type for the app servers"
  type        = string
  default     = "t3.micro"
}

variable "asg_min" {
  type    = number
  default = 2
}

variable "asg_max" {
  type    = number
  default = 4
}

variable "cpu_target" {
  description = "Average CPU % the Auto Scaling group tries to hold"
  type        = number
  default     = 70
}

variable "db_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "db_multi_az" {
  description = "Run RDS in Multi-AZ mode. Costs about double; set false while developing."
  type        = bool
  default     = true
}

variable "alert_email" {
  description = "Email that receives CloudWatch alarm notifications"
  type        = string
}
