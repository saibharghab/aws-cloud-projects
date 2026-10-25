"""Daily AWS account checker.

Flags common security and hygiene problems, saves a JSON report to S3,
and emails a short summary through SNS.

Checks:
  1. Security groups that allow SSH (22) or RDP (3389) from anywhere
  2. S3 buckets that are not fully protected by Block Public Access
  3. EBS volumes that are not encrypted
  4. EC2 instances missing the required owner tag
"""
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

RISKY_PORTS = {22: "SSH", 3389: "RDP"}
OPEN_RANGES = {"0.0.0.0/0", "::/0"}
REQUIRED_TAG = os.environ.get("REQUIRED_TAG", "Owner")


def open_security_groups(ec2):
    findings = []
    for page in ec2.get_paginator("describe_security_groups").paginate():
        for sg in page["SecurityGroups"]:
            for rule in sg.get("IpPermissions", []):
                ranges = {r["CidrIp"] for r in rule.get("IpRanges", [])}
                ranges |= {r["CidrIpv6"] for r in rule.get("Ipv6Ranges", [])}
                if not ranges & OPEN_RANGES:
                    continue
                all_traffic = rule.get("IpProtocol") == "-1"
                for port, name in RISKY_PORTS.items():
                    in_range = rule.get("FromPort", 0) <= port <= rule.get("ToPort", 65535)
                    if all_traffic or in_range:
                        findings.append({
                            "groupId": sg["GroupId"],
                            "groupName": sg.get("GroupName"),
                            "port": port,
                            "service": name,
                        })
    return findings


def account_blocks_public_access(account_id, region):
    s3control = boto3.client("s3control", region_name=region)
    try:
        cfg = s3control.get_public_access_block(AccountId=account_id)
        return all(cfg["PublicAccessBlockConfiguration"].values())
    except ClientError:
        return False


def public_buckets(s3, account_id, region):
    if account_blocks_public_access(account_id, region):
        return []  # account-wide setting already protects every bucket
    findings = []
    for bucket in s3.list_buckets().get("Buckets", []):
        name = bucket["Name"]
        try:
            cfg = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
            missing = [k for k, v in cfg.items() if not v]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchPublicAccessBlockConfiguration":
                missing = ["all settings off"]
            else:
                missing = [f"could not read: {e.response['Error']['Code']}"]
        if missing:
            findings.append({"bucket": name, "missing": missing})
    return findings


def unencrypted_volumes(ec2):
    findings = []
    paginator = ec2.get_paginator("describe_volumes")
    for page in paginator.paginate(Filters=[{"Name": "encrypted", "Values": ["false"]}]):
        for v in page["Volumes"]:
            attached = [a["InstanceId"] for a in v.get("Attachments", [])]
            findings.append({"volumeId": v["VolumeId"], "sizeGb": v["Size"], "attachedTo": attached})
    return findings


def untagged_instances(ec2):
    findings = []
    paginator = ec2.get_paginator("describe_instances")
    filters = [{"Name": "instance-state-name", "Values": ["pending", "running", "stopped"]}]
    for page in paginator.paginate(Filters=filters):
        for res in page["Reservations"]:
            for inst in res["Instances"]:
                tags = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
                if not tags.get(REQUIRED_TAG):
                    findings.append({
                        "instanceId": inst["InstanceId"],
                        "type": inst["InstanceType"],
                        "name": tags.get("Name", ""),
                    })
    return findings


def build_summary(report):
    lines = [f"AWS account check for {report['accountId']} ({report['region']}) on {report['date']}", ""]
    labels = {
        "openSecurityGroups": "Security groups open to SSH/RDP from anywhere",
        "publicBuckets": "S3 buckets without full Block Public Access",
        "unencryptedVolumes": "Unencrypted EBS volumes",
        "untaggedInstances": f"EC2 instances missing the '{REQUIRED_TAG}' tag",
    }
    for key, label in labels.items():
        items = report["findings"][key]
        lines.append(f"{label}: {len(items)}")
        for item in items[:10]:
            lines.append("  - " + ", ".join(f"{k}={v}" for k, v in item.items()))
        if len(items) > 10:
            lines.append(f"  ...and {len(items) - 10} more (see full report)")
    lines += ["", f"Full report: s3://{report['bucket']}/{report['key']}"]
    return "\n".join(lines)


def handler(event, context):
    region = os.environ.get("AWS_REGION", "us-east-1")
    bucket = os.environ["REPORT_BUCKET"]
    topic = os.environ["TOPIC_ARN"]

    account_id = boto3.client("sts").get_caller_identity()["Account"]
    ec2 = boto3.client("ec2", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    findings = {
        "openSecurityGroups": open_security_groups(ec2),
        "publicBuckets": public_buckets(s3, account_id, region),
        "unencryptedVolumes": unencrypted_volumes(ec2),
        "untaggedInstances": untagged_instances(ec2),
    }
    total = sum(len(v) for v in findings.values())
    now = datetime.now(timezone.utc)
    key = f"reports/{now:%Y/%m/%d}/report-{now:%H%M%S}.json"

    report = {
        "accountId": account_id,
        "region": region,
        "date": now.date().isoformat(),
        "totalFindings": total,
        "findings": findings,
        "bucket": bucket,
        "key": key,
    }
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(report, indent=2),
                  ContentType="application/json")

    subject = f"AWS check: {total} finding(s)" if total else "AWS check: all clear"
    boto3.client("sns", region_name=region).publish(
        TopicArn=topic, Subject=subject, Message=build_summary(report)
    )
    print(json.dumps({"totalFindings": total, "reportKey": key}))
    return {"totalFindings": total, "reportKey": key}
