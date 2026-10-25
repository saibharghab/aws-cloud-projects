"""Stops development EC2 instances at night and starts them in the morning.

Only instances tagged Schedule=office-hours are touched. EventBridge calls
this function with {"action": "stop"} in the evening and {"action": "start"}
on weekday mornings.
"""
import json
import os

import boto3

TAG_KEY = os.environ.get("SCHEDULE_TAG_KEY", "Schedule")
TAG_VALUE = os.environ.get("SCHEDULE_TAG_VALUE", "office-hours")


def find_instances(ec2, state):
    ids = []
    filters = [
        {"Name": f"tag:{TAG_KEY}", "Values": [TAG_VALUE]},
        {"Name": "instance-state-name", "Values": [state]},
    ]
    for page in ec2.get_paginator("describe_instances").paginate(Filters=filters):
        for res in page["Reservations"]:
            ids += [i["InstanceId"] for i in res["Instances"]]
    return ids


def handler(event, context):
    action = (event or {}).get("action")
    if action not in ("stop", "start"):
        raise ValueError('event must be {"action": "stop"} or {"action": "start"}')

    ec2 = boto3.client("ec2", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    if action == "stop":
        ids = find_instances(ec2, "running")
        if ids:
            ec2.stop_instances(InstanceIds=ids)
    else:
        ids = find_instances(ec2, "stopped")
        if ids:
            ec2.start_instances(InstanceIds=ids)

    result = {"action": action, "count": len(ids), "instanceIds": ids}
    print(json.dumps(result))
    return result
