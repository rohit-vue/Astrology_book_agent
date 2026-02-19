# FILE: src/assemble_payload/app.py (ROBUST FINAL VERSION)
import json

def lambda_handler(event, context):
    """
    Receives the Step Function state.
    Robustly extracts order data, falling back to the book results array 
    if the root state was overwritten.
    """
    print(f"AssemblePayload received raw event: {json.dumps(event)}")
    
    data = event.get('Payload', event)
    
    processed_books_results = data.get('processed_books_results', [])
    
    order_id = data.get('order_id')
    if not order_id and processed_books_results:
        print("WARNING: Root order_id missing. Rescuing from processed_books_results.")
        order_id = processed_books_results[0].get('order_id')
        
    shipping_address = data.get('shipping_address')
    if not shipping_address and processed_books_results:
        print("WARNING: Root shipping_address missing. Rescuing from processed_books_results.")
        shipping_address = processed_books_results[0].get('shipping_address')

    customer_details = data.get('customer_details')
    if not customer_details and processed_books_results:
        print("WARNING: Root customer_details missing. Rescuing from processed_books_results.")
        customer_details = processed_books_results[0].get('customer_details') or {}

    final_payload = {
        "order_id": order_id,
        "shipping_address": shipping_address,
        "customer_details": customer_details or {},
        "processed_books_results": processed_books_results
    }
    
    print(f"Assembled clean payload: {json.dumps(final_payload)}")
    return final_payload