"""Runs every Lambda function from template.yaml against a mocked DynamoDB.

Install: pip install boto3 moto pytest pyyaml
Run:     pytest -v
"""
import json
import os
import pathlib
import types

import boto3
import pytest
import yaml
from moto import mock_aws

TEMPLATE = pathlib.Path(__file__).resolve().parent.parent / "template.yaml"


class CfnLoader(yaml.SafeLoader):
    """YAML loader that tolerates CloudFormation tags like !Ref and !Sub."""


CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def load_functions():
    tpl = yaml.load(TEMPLATE.read_text(), Loader=CfnLoader)
    return {
        name: res["Properties"]["Code"]["ZipFile"]
        for name, res in tpl["Resources"].items()
        if res["Type"] == "AWS::Lambda::Function"
    }


@pytest.fixture
def api():
    os.environ.update(
        AWS_DEFAULT_REGION="us-east-1",
        AWS_ACCESS_KEY_ID="test",
        AWS_SECRET_ACCESS_KEY="test",
        TABLE_NAME="tasks",
    )
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="tasks",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        )
        handlers = {}
        for name, code in load_functions().items():
            mod = types.ModuleType(name)
            exec(compile(code, name, "exec"), mod.__dict__)
            handlers[name.replace("Function", "")] = mod.handler

        def call(fn, body=None, task_id=None, query=None):
            event = {
                "body": json.dumps(body) if body is not None else None,
                "pathParameters": {"id": task_id} if task_id else None,
                "queryStringParameters": query,
            }
            res = handlers[fn](event, None)
            return res["statusCode"], json.loads(res["body"]) if res["body"] else None

        yield call


def test_full_lifecycle(api):
    code, task = api("CreateTask", {"title": "Write README"})
    assert code == 201 and task["status"] == "todo"

    code, got = api("GetTask", task_id=task["id"])
    assert code == 200 and got["title"] == "Write README"

    code, upd = api("UpdateTask", {"status": "done"}, task_id=task["id"])
    assert code == 200 and upd["status"] == "done" and upd["title"] == "Write README"

    code, listed = api("ListTasks")
    assert code == 200 and listed["count"] == 1

    code, _ = api("DeleteTask", task_id=task["id"])
    assert code == 204

    code, _ = api("GetTask", task_id=task["id"])
    assert code == 404


def test_list_filter_by_status(api):
    api("CreateTask", {"title": "a"})
    api("CreateTask", {"title": "b", "status": "done"})
    code, listed = api("ListTasks", query={"status": "done"})
    assert code == 200 and listed["count"] == 1 and listed["tasks"][0]["title"] == "b"


@pytest.mark.parametrize(
    "body",
    [{}, {"title": "  "}, {"title": "x" * 201}, {"title": "ok", "status": "later"}],
)
def test_create_rejects_bad_input(api, body):
    code, err = api("CreateTask", body)
    assert code == 400 and "error" in err


def test_update_missing_task_returns_404_and_creates_nothing(api):
    code, _ = api("UpdateTask", {"title": "ghost"}, task_id="does-not-exist")
    assert code == 404
    assert api("ListTasks")[1]["count"] == 0


def test_update_needs_fields(api):
    _, task = api("CreateTask", {"title": "t"})
    code, _ = api("UpdateTask", {}, task_id=task["id"])
    assert code == 400


def test_delete_missing_returns_404(api):
    code, _ = api("DeleteTask", task_id="nope")
    assert code == 404


def test_inline_code_under_lambda_limit():
    for name, code in load_functions().items():
        assert len(code.encode()) < 4096, f"{name} inline code is over 4 KB"
