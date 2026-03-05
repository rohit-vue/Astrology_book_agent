# src/start_execution/app.py
import boto3
import json
import os
from datetime import datetime, timezone

# Initialize the Step Functions client
sfn_client = boto3.client('stepfunctions')
sqs_client = boto3.client('sqs')

# Get the ARN of the state machine from an environment variable
STATE_MACHINE_ARN = os.environ['STATE_MACHINE_ARN']
BOOK_ORDERS_QUEUE_URL = os.environ.get('BOOK_ORDERS_QUEUE_URL')

def parse_iso_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def lambda_handler(event, context):
    """
    Triggered by SQS. Loops through messages and starts a Step Function execution for each.
    """
    print(f"Received {len(event.get('Records', []))} records from SQS.")

    for record in event.get('Records', []):
        try:
            message_body = json.loads(record['body'])
            order_id = message_body.get('order_id')

            if not order_id:
                print("ERROR: SQS message is missing 'order_id'. Skipping.")
                continue

            scheduled_start = message_body.get('factory_start_at')
            if scheduled_start:
                target_dt = None
                try:
                    target_dt = parse_iso_datetime(scheduled_start)
                except Exception as schedule_error:
                    print(f"WARNING: Could not evaluate factory_start_at for order {order_id}: {schedule_error}")

                if target_dt:
                    now_utc = datetime.now(timezone.utc)
                    remaining_seconds = int((target_dt.astimezone(timezone.utc) - now_utc).total_seconds())
                    if remaining_seconds > 0:
                        if not BOOK_ORDERS_QUEUE_URL:
                            raise ValueError("BOOK_ORDERS_QUEUE_URL is required for delayed scheduling.")
                        delay_seconds = min(900, remaining_seconds)
                        print(f"Order {order_id} not due yet. Requeueing for {delay_seconds}s.")
                        sqs_client.send_message(
                            QueueUrl=BOOK_ORDERS_QUEUE_URL,
                            MessageBody=json.dumps(message_body),
                            DelaySeconds=delay_seconds
                        )
                        continue

            print(f"Starting Step Function execution for order_id: {order_id}")

            sfn_client.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=order_id,
                input=json.dumps(message_body)
            )

        except Exception as e:
            print(f"ERROR: Failed to start execution for record: {record}. Error: {e}")
            raise e 

    return {'statusCode': 200, 'body': 'Successfully started executions.'}