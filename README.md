# AWS Cloud Projects

Three hands-on AWS projects covering networking, high availability, serverless, infrastructure as code, security and cost control.

| # | Project | Main services | IaC | Cost to run |
| --- | --- | --- | --- | --- |
| 1 | [Highly Available Web Application](01-ha-web-app) | VPC, ALB, EC2 Auto Scaling, RDS Multi-AZ, CloudWatch | Terraform | ~$3-4/day, destroy after testing |
| 2 | [Serverless Task Tracker API](02-serverless-task-api) | API Gateway, Lambda, DynamoDB, IAM | CloudFormation | Free tier |
| 3 | [Security and Cost Checker](03-security-cost-checker) | Lambda, EventBridge, SNS, S3, IAM | Terraform | Free tier |

## One-time setup

1. **Protect your bill first.** In the AWS console go to Billing → Budgets → create a monthly cost budget of $10 with an email alert.
2. **Don't use the root account day to day.** Turn on MFA for root, then create an IAM user (or IAM Identity Center user) with `AdministratorAccess` for yourself and use that.
3. **Install tools:** [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [Terraform](https://developer.hashicorp.com/terraform/install), Python 3.12, Git.
4. **Configure the CLI:** `aws configure` (region `us-east-1`).

## Suggested order

1. **Project 2** first: cheapest, quickest win, teaches Lambda and IAM.
2. **Project 3** next: more Lambda plus Terraform basics.
3. **Project 1** last: the biggest one. Deploy, test, screenshot, and `terraform destroy` the same day.

## Run the tests

Projects 2 and 3 have tests that run against a mocked AWS, so you need no account:

```bash
pip install boto3 moto pytest pyyaml cfn-lint
(cd 02-serverless-task-api && pytest -v && cfn-lint template.yaml)
(cd 03-security-cost-checker && pytest -v)
```
