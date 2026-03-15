"""Traitement des événements Stripe (abonnements, annulations)."""

async def handle_stripe_event(event: dict, db):
    etype = event["type"]
    data  = event["data"]["object"]

    if etype == "checkout.session.completed":
        user_id = data.get("metadata",{}).get("user_id")
        plan    = data.get("metadata",{}).get("plan","starter")
        customer_id = data.get("customer")
        sub_id      = data.get("subscription")
        if user_id:
            await db.execute(
                "UPDATE users SET plan=$1, stripe_customer_id=$2, stripe_subscription_id=$3 WHERE id=$4",
                plan, customer_id, sub_id, int(user_id))

    elif etype in ("customer.subscription.deleted", "customer.subscription.paused"):
        customer_id = data.get("customer")
        await db.execute(
            "UPDATE users SET plan='free' WHERE stripe_customer_id=$1", customer_id)

    elif etype == "customer.subscription.updated":
        customer_id = data.get("customer")
        status = data.get("status")
        if status == "active":
            # Retrouver le plan depuis les metadata ou le price_id
            items = data.get("items",{}).get("data",[])
            price_id = items[0]["price"]["id"] if items else ""
            plan = _price_to_plan(price_id)
            await db.execute(
                "UPDATE users SET plan=$1 WHERE stripe_customer_id=$2", plan, customer_id)

def _price_to_plan(price_id: str) -> str:
    import os
    mapping = {
        os.getenv("STRIPE_PRICE_STARTER",""):  "starter",
        os.getenv("STRIPE_PRICE_PRO",""):       "pro",
        os.getenv("STRIPE_PRICE_BUSINESS",""): "business",
    }
    return mapping.get(price_id, "starter")
