terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}

# ------------------------------------------------------------------ S3 reports
resource "aws_s3_bucket" "reports" {
  bucket        = "${var.project}-reports-${data.aws_caller_identity.current.account_id}"
  force_destroy = true # lets terraform destroy remove old reports too
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    id     = "expire-old-reports"
    status = "Enabled"
    filter {
      prefix = "reports/"
    }
    expiration {
      days = var.report_retention_days
    }
  }
}

# ------------------------------------------------------------------ SNS
resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ------------------------------------------------------------------ Lambda packages
data "archive_file" "checker" {
  type        = "zip"
  source_file = "${path.module}/src/checker.py"
  output_path = "${path.module}/build/checker.zip"
}

data "archive_file" "scheduler" {
  type        = "zip"
  source_file = "${path.module}/src/scheduler.py"
  output_path = "${path.module}/build/scheduler.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ------------------------------------------------------------------ Checker function
resource "aws_iam_role" "checker" {
  name               = "${var.project}-checker-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "checker_logs" {
  role       = aws_iam_role.checker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "checker" {
  statement {
    sid = "ReadOnlyInventory"
    actions = [
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeVolumes",
      "ec2:DescribeInstances",
      "s3:ListAllMyBuckets",
      "s3:GetBucketPublicAccessBlock",
      "s3:GetAccountPublicAccessBlock",
    ]
    resources = ["*"] # these describe/list calls do not support resource-level limits
  }

  statement {
    sid       = "WriteReports"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.reports.arn}/reports/*"]
  }

  statement {
    sid       = "SendAlert"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_iam_role_policy" "checker" {
  name   = "checker-permissions"
  role   = aws_iam_role.checker.id
  policy = data.aws_iam_policy_document.checker.json
}

resource "aws_lambda_function" "checker" {
  function_name    = "${var.project}-checker"
  role             = aws_iam_role.checker.arn
  runtime          = "python3.12"
  handler          = "checker.handler"
  filename         = data.archive_file.checker.output_path
  source_code_hash = data.archive_file.checker.output_base64sha256
  timeout          = 120
  memory_size      = 256

  environment {
    variables = {
      REPORT_BUCKET = aws_s3_bucket.reports.bucket
      TOPIC_ARN     = aws_sns_topic.alerts.arn
      REQUIRED_TAG  = var.required_tag
    }
  }
}

resource "aws_cloudwatch_log_group" "checker" {
  name              = "/aws/lambda/${aws_lambda_function.checker.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_event_rule" "daily_check" {
  name                = "${var.project}-daily-check"
  description         = "Run the account checker once a day"
  schedule_expression = var.check_schedule
}

resource "aws_cloudwatch_event_target" "daily_check" {
  rule = aws_cloudwatch_event_rule.daily_check.name
  arn  = aws_lambda_function.checker.arn
}

resource "aws_lambda_permission" "daily_check" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.checker.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_check.arn
}

# ------------------------------------------------------------------ Scheduler function
resource "aws_iam_role" "scheduler" {
  name               = "${var.project}-scheduler-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "scheduler_logs" {
  role       = aws_iam_role.scheduler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "scheduler" {
  statement {
    sid       = "FindInstances"
    actions   = ["ec2:DescribeInstances"]
    resources = ["*"]
  }

  # Can only stop/start instances that carry the schedule tag.
  statement {
    sid       = "StopStartTaggedOnly"
    actions   = ["ec2:StopInstances", "ec2:StartInstances"]
    resources = ["arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/Schedule"
      values   = ["office-hours"]
    }
  }
}

resource "aws_iam_role_policy" "scheduler" {
  name   = "scheduler-permissions"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler.json
}

resource "aws_lambda_function" "scheduler" {
  function_name    = "${var.project}-scheduler"
  role             = aws_iam_role.scheduler.arn
  runtime          = "python3.12"
  handler          = "scheduler.handler"
  filename         = data.archive_file.scheduler.output_path
  source_code_hash = data.archive_file.scheduler.output_base64sha256
  timeout          = 60
  memory_size      = 128
}

resource "aws_cloudwatch_log_group" "scheduler" {
  name              = "/aws/lambda/${aws_lambda_function.scheduler.function_name}"
  retention_in_days = 14
}

resource "aws_cloudwatch_event_rule" "stop" {
  name                = "${var.project}-stop-dev"
  description         = "Stop office-hours instances every evening"
  schedule_expression = var.stop_schedule
}

resource "aws_cloudwatch_event_rule" "start" {
  name                = "${var.project}-start-dev"
  description         = "Start office-hours instances on weekday mornings"
  schedule_expression = var.start_schedule
}

resource "aws_cloudwatch_event_target" "stop" {
  rule  = aws_cloudwatch_event_rule.stop.name
  arn   = aws_lambda_function.scheduler.arn
  input = jsonencode({ action = "stop" })
}

resource "aws_cloudwatch_event_target" "start" {
  rule  = aws_cloudwatch_event_rule.start.name
  arn   = aws_lambda_function.scheduler.arn
  input = jsonencode({ action = "start" })
}

resource "aws_lambda_permission" "stop" {
  statement_id  = "AllowStopRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.stop.arn
}

resource "aws_lambda_permission" "start" {
  statement_id  = "AllowStartRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scheduler.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.start.arn
}

# ------------------------------------------------------------------ Alarm on failures
resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = {
    checker   = aws_lambda_function.checker.function_name
    scheduler = aws_lambda_function.scheduler.function_name
  }

  alarm_name          = "${each.value}-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = each.value }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
