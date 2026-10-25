"""Tests for the checker and scheduler Lambda functions using a mocked AWS.

Install: pip install boto3 moto pytest
Run:     pytest -v
"""
import json
import os
import sys
import pathlib

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

REGION = "us-east-1"


@pytest.fixture
def aws():
    os.environ.update(
        AWS_DEFAULT_REGION=REGION,
        AWS_REGION=REGION,
        AWS_ACCESS_KEY_ID="test",
        AWS_SECRET_ACCESS_KEY="test",
    )
    with mock_aws():
        yield


def ami(ec2):
    return ec2.describe_images(Owners=["amazon"])["Images"][0]["ImageId"]


def test_checker_finds_each_problem(aws):
    import checker

    ec2 = boto3.client("ec2", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    sns = boto3.client("sns", region_name=REGION)
    sqs = boto3.client("sqs", region_name=REGION)

    # report bucket + topic (with an SQS subscriber so we can read the message)
    s3.create_bucket(Bucket="reports")
    topic = sns.create_topic(Name="alerts")["TopicArn"]
    q_url = sqs.create_queue(QueueName="inbox")["QueueUrl"]
    q_arn = sqs.get_queue_attributes(QueueUrl=q_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
    sns.subscribe(TopicArn=topic, Protocol="sqs", Endpoint=q_arn)

    # 1. open SSH, plus a safe group that should NOT be flagged
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    bad = ec2.create_security_group(GroupName="bad", Description="x", VpcId=vpc)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=bad, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    ok = ec2.create_security_group(GroupName="ok", Description="x", VpcId=vpc)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=ok, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])

    # 2. a bucket with no public access block
    s3.create_bucket(Bucket="leaky-bucket")

    # 3. unencrypted volume
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=8, Encrypted=False)

    # 4. one tagged, one untagged instance
    image = ami(ec2)
    ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, TagSpecifications=[{
        "ResourceType": "instance", "Tags": [{"Key": "Owner", "Value": "sai"}]}])
    untagged = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1)["Instances"][0]["InstanceId"]

    os.environ.update(REPORT_BUCKET="reports", TOPIC_ARN=topic)
    result = checker.handler({}, None)

    report = json.loads(s3.get_object(Bucket="reports", Key=result["reportKey"])["Body"].read())
    f = report["findings"]

    assert [g["groupId"] for g in f["openSecurityGroups"]] == [bad]
    assert any(b["bucket"] == "leaky-bucket" for b in f["publicBuckets"])
    assert all(b["bucket"] != "reports" or b["missing"] for b in f["publicBuckets"])
    assert any(v["sizeGb"] == 8 for v in f["unencryptedVolumes"])
    assert [i["instanceId"] for i in f["untaggedInstances"]] == [untagged]

    msg = json.loads(sqs.receive_message(QueueUrl=q_url)["Messages"][0]["Body"])
    assert "finding(s)" in msg["Subject"]
    assert "Security groups open to SSH/RDP from anywhere: 1" in msg["Message"]


def test_checker_all_traffic_rule_is_flagged(aws):
    import checker

    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    sg = ec2.create_security_group(GroupName="wide", Description="x", VpcId=vpc)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
        "IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    found = [x for x in checker.open_security_groups(ec2) if x["groupId"] == sg]
    assert {x["service"] for x in found} == {"SSH", "RDP"}


def test_scheduler_only_touches_tagged_instances(aws):
    import scheduler

    ec2 = boto3.client("ec2", region_name=REGION)
    image = ami(ec2)
    dev = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, TagSpecifications=[{
        "ResourceType": "instance", "Tags": [{"Key": "Schedule", "Value": "office-hours"}]}])["Instances"][0]["InstanceId"]
    prod = ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1)["Instances"][0]["InstanceId"]

    def state(i):
        return ec2.describe_instances(InstanceIds=[i])["Reservations"][0]["Instances"][0]["State"]["Name"]

    out = scheduler.handler({"action": "stop"}, None)
    assert out["instanceIds"] == [dev]
    assert state(dev) == "stopped" and state(prod) == "running"

    out = scheduler.handler({"action": "start"}, None)
    assert out["instanceIds"] == [dev]
    assert state(dev) == "running"


def test_scheduler_rejects_bad_event(aws):
    import scheduler

    with pytest.raises(ValueError):
        scheduler.handler({"action": "reboot"}, None)
