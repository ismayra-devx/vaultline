import uuid
from dotenv import load_dotenv
from backend.paypal.client import PayPalConfig, PayPalClient

load_dotenv(".env")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Create and capture PayPal Sandbox checkout order")
    parser.add_argument("--amount", default="30.00", help="Order amount in USD (default: 30.00)")
    args = parser.parse_args()

    order_amount = str(args.amount).strip()

    with PayPalClient(PayPalConfig.from_env()) as paypal:
        token = paypal.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
            "PayPal-Request-Id": str(uuid.uuid4()),
        }

        order_response = paypal._client.post(
            paypal._build_url("/v2/checkout/orders"),
            headers=headers,
            json={
                "intent": "CAPTURE",
                "purchase_units": [{
                    "amount": {
                        "currency_code": "USD",
                        "value": order_amount,
                    },
                    "description": "Vaultline Sandbox test payment"
                }],
                "payment_source": {
                    "paypal": {
                        "experience_context": {
                            "return_url": "https://example.com/success",
                            "cancel_url": "https://example.com/cancel",
                            "user_action": "PAY_NOW"
                        }
                    }
                }
            },
        )

        if order_response.status_code not in (200, 201):
            raise RuntimeError(
                f"Order creation failed ({order_response.status_code}): "
                f"{order_response.text[:1000]}"
            )

        order = order_response.json()
        order_id = order["id"]
        approval_url = next(
            (link["href"] for link in order.get("links", [])
             if link.get("rel") in ("approve", "payer-action")),
            None
        )

        if not approval_url:
            raise RuntimeError("PayPal did not return an approval URL.")

        print("\nORDER CREATED:", order_id)
        print(f"\nOpen this URL and approve the ${order_amount} Sandbox payment:\n")
        print(approval_url)

        input("\nAfter approving the payment in your browser, press Enter here...")

        capture_response = paypal._client.post(
            paypal._build_url(f"/v2/checkout/orders/{order_id}/capture"),
            headers={
                **headers,
                "PayPal-Request-Id": str(uuid.uuid4()),
            },
            json={}
        )

        if capture_response.status_code not in (200, 201):
            raise RuntimeError(
                f"Capture failed ({capture_response.status_code}): "
                f"{capture_response.text[:1000]}"
            )

        result = capture_response.json()
        captures = [
            capture
            for unit in result.get("purchase_units", [])
            for capture in unit.get("payments", {}).get("captures", [])
        ]

        completed = next(
            (capture for capture in captures
             if capture.get("status") == "COMPLETED"),
            None
        )

        if not completed:
            raise RuntimeError(
                "No completed capture returned. Check the Sandbox order status."
            )

        print("\nPAYMENT CAPTURED SUCCESSFULLY")
        print("Capture ID:", completed["id"])
        print("Amount:", completed["amount"]["value"])
        print("Currency:", completed["amount"]["currency_code"])
        print("Status:", completed["status"])

if __name__ == "__main__":
    main()
