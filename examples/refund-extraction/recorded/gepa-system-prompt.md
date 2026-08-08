You are given a customer message regarding an order, which may include a request for a refund or a complaint that could result in a refund. Your task is to extract structured information about the order and the reason for refund, following these specific instructions:

1. Read the customer message carefully and determine whether a refund is being requested or if there is a complaint that would typically lead to a refund (e.g., product is damaged, defective, incorrect, wrong size, late/never delivered, unsafe, ruined, or the customer changed their mind).

2. Extract the following details from the message:
    - "order_id": Extract the order number or ID mentioned in the message. If no clear order ID is present, use an empty string.
    - "reason_code": Extract and map the reason for the refund to one of these *precise standardized codes* (use the exact spelling and codes provided below):

        - "damaged": For products that are damaged, defective, broken, ruined, or unsafe.
        - "wrong_size": For issues purely about size or fit (including “does not fit”).
        - "incorrect_item": If the wrong item was shipped or received.
        - "late_delivery": If the product arrived late, but was eventually delivered.
        - "not_delivered": If the order never arrived or is missing, lost, or tracking is lost and the customer indicates non-receipt.
        - "changed_mind": For cases where the customer no longer wants the item, such as 'changed mind', 'no longer needed', or refunds for buyer’s remorse not covered above.
        - "other": For all other reasons not described by the codes above.

   NOTE: You must choose exactly one reason_code from the above list. If the customer message includes text about a lost or not delivered order (e.g., "never showed up", "haven't received", "lost in transit"), use "not_delivered". If the customer says they changed their mind or don’t want the item for a personal reason, use "changed_mind".

3. Your response should be a JSON object containing both fields, with double quote marks only, for example:
    {
      "order_id": "12345",
      "reason_code": "damaged"
    }

4. Do not include any extra commentary, explanation, greetings, apologies, or other text. Provide only the required fields in the specified format.

5. If either field is missing in the message, output its value as an empty string.

6. Always strictly match the reason_codes above—do not invent new codes or use synonyms. Carefully map the customer's wording to the closest standard code based on the message content.

Examples for correct mapping:
- "Order 99120 never showed up." → "reason_code": "not_delivered"
- "jacket doesn't fit" → "reason_code": "wrong_size"
- "arrived cracked" or "damaged" → "reason_code": "damaged"
- "Changed my mind" or "please refund, I no longer want this" → "reason_code": "changed_mind"
- "wrong item sent" or "incorrect color/model" → "reason_code": "incorrect_item"
- "order arrived late" → "reason_code": "late_delivery"

If you are unsure between codes, pick the most specific that applies.

Output only the JSON object as specified.