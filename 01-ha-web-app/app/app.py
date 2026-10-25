"""Small Flask app that proves the whole stack works.

Each page view is saved in MySQL, and the page shows which EC2 instance
and Availability Zone answered, so refreshing shows the load balancer at work.
"""
import json
import os
import urllib.request

import boto3
import pymysql
from flask import Flask, jsonify

app = Flask(__name__)

_creds = None


def db_credentials():
    """Read the RDS-managed username/password from Secrets Manager once."""
    global _creds
    if _creds is None:
        sm = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"])
        secret = sm.get_secret_value(SecretId=os.environ["DB_SECRET_ARN"])
        _creds = json.loads(secret["SecretString"])
    return _creds


def db():
    c = db_credentials()
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        user=c["username"],
        password=c["password"],
        database=os.environ["DB_NAME"],
        connect_timeout=5,
        autocommit=True,
    )


def instance_metadata(path):
    """Read EC2 instance metadata using IMDSv2 (token required)."""
    try:
        token_req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token",
            method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
        )
        token = urllib.request.urlopen(token_req, timeout=2).read().decode()
        req = urllib.request.Request(
            f"http://169.254.169.254/latest/meta-data/{path}",
            headers={"X-aws-ec2-metadata-token": token},
        )
        return urllib.request.urlopen(req, timeout=2).read().decode()
    except Exception:
        return "unknown"


@app.route("/health")
def health():
    return "ok", 200


@app.route("/")
def index():
    instance_id = instance_metadata("instance-id")
    az = instance_metadata("placement/availability-zone")

    conn = db()
    with conn.cursor() as cur:
        cur.execute(
            """CREATE TABLE IF NOT EXISTS visits (
                   id INT AUTO_INCREMENT PRIMARY KEY,
                   instance_id VARCHAR(32),
                   visited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
        )
        cur.execute("INSERT INTO visits (instance_id) VALUES (%s)", (instance_id,))
        cur.execute("SELECT COUNT(*) FROM visits")
        total = cur.fetchone()[0]
    conn.close()

    return f"""
    <html><body style="font-family: sans-serif; max-width: 600px; margin: 40px auto;">
      <h1>Highly Available Web App</h1>
      <p>Served by instance <b>{instance_id}</b> in <b>{az}</b>.</p>
      <p>Total visits stored in RDS MySQL: <b>{total}</b></p>
      <p>Refresh to see the load balancer switch between instances.</p>
    </body></html>
    """


@app.route("/api/stats")
def stats():
    conn = db()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT instance_id, COUNT(*) FROM visits GROUP BY instance_id"
        )
        rows = cur.fetchall()
    conn.close()
    return jsonify({r[0]: r[1] for r in rows})
