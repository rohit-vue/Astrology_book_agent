# src/start_execution/app.py
import boto3
import json
import os

# Initialize the Step Functions client
sfn_client = boto3.client('stepfunctions')

# Get the ARN of the state machine from an environment variable
STATE_MACHINE_ARN = os.environ['STATE_MACHINE_ARN']

def lambda_handler(event, context):
    """
    Triggered by SQS. Loops through messages and starts a Step Function execution for each.
    """
    print(f"Received {len(event.get('Records', []))} records from SQS.")

    for record in event.get('Records', []):
        try:
            # The message body from our ingestion Lambda is a JSON string
            message_body = json.loads(record['body'])
            order_id = message_body.get('order_id')

            if not order_id:
                print("ERROR: SQS message is missing 'order_id'. Skipping.")
                continue

            print(f"Starting Step Function execution for order_id: {order_id}")

            # Start the state machine execution
            sfn_client.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=order_id,  # Using order_id as the name prevents duplicate executions for the same order
                input=json.dumps(message_body)
            )

        except Exception as e:
            print(f"ERROR: Failed to start execution for record: {record}. Error: {e}")
            # The message will become visible in the queue again for a retry.
            # If it fails repeatedly, the DLQ will catch it.
            raise e # Re-raise the exception to signal failure to the SQS service

    return {'statusCode': 200, 'body': 'Successfully started executions.'}