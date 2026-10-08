import os
import json
import time
import logging
import threading
from django.db import models, close_old_connections

logger = logging.getLogger(__name__)


def extract_shopify_variables(payload, topic=""):
    """
    Standardizes and flattens variables from Shopify webhook payloads
    so they can be dynamically replaced as {{ variable_name }} in email templates.
    """
    customer = payload.get("customer") or {}
    billing = payload.get("billing_address") or {}
    shipping = payload.get("shipping_address") or {}
    default_address = customer.get("default_address") or {}

    first_name = (
        customer.get("first_name")
        or billing.get("first_name")
        or shipping.get("first_name")
        or default_address.get("first_name")
        or ""
    )
    last_name = (
        customer.get("last_name")
        or billing.get("last_name")
        or shipping.get("last_name")
        or default_address.get("last_name")
        or ""
    )
    customer_name = f"{first_name} {last_name}".strip() or customer.get("name") or "Valued Customer"

    email = (
        payload.get("email")
        or payload.get("contact_email")
        or customer.get("email")
        or ""
    )

    # Order numbers
    order_number = str(payload.get("order_number") or payload.get("name") or payload.get("id") or "")
    order_name = str(payload.get("name") or (f"#{order_number}" if order_number else ""))

    # Pricing
    total_price = str(payload.get("total_price") or payload.get("current_total_price") or payload.get("subtotal_price") or "0.00")
    subtotal_price = str(payload.get("subtotal_price") or total_price)
    total_tax = str(payload.get("total_tax") or "0.00")
    total_discounts = str(payload.get("total_discounts") or "0.00")
    currency = str(payload.get("currency") or payload.get("presentment_currency") or "USD")

    # Shipping details
    shipping_street = shipping.get("address1") or billing.get("address1") or ""
    shipping_city = shipping.get("city") or billing.get("city") or ""
    shipping_zip = shipping.get("zip") or billing.get("zip") or ""
    shipping_country = shipping.get("country") or billing.get("country") or ""

    # Checkout & Cart Recovery URL
    abandoned_url = payload.get("abandoned_checkout_url") or payload.get("landing_site") or ""

    # Line items summary
    line_items = payload.get("line_items") or []
    item_titles = [item.get("title") for item in line_items if item.get("title")]
    items_summary = ", ".join(item_titles) if item_titles else "Your selected items"

    variables = {
        "first_name": first_name,
        "last_name": last_name,
        "customer_name": customer_name,
        "email": email,
        "customer_email": email,
        "order_number": order_number,
        "order_name": order_name,
        "total_price": total_price,
        "subtotal_price": subtotal_price,
        "total_tax": total_tax,
        "total_discounts": total_discounts,
        "currency": currency,
        "financial_status": str(payload.get("financial_status") or ""),
        "fulfillment_status": str(payload.get("fulfillment_status") or "unfulfilled"),
        "shipping_address": shipping_street,
        "shipping_city": shipping_city,
        "shipping_zip": shipping_zip,
        "shipping_country": shipping_country,
        "abandoned_checkout_url": abandoned_url,
        "recovery_url": abandoned_url,
        "checkout_url": abandoned_url,
        "items_summary": items_summary,
        "items_count": str(len(line_items)),
    }

    # Pass all top-level primitives from Shopify payload as available variables
    for k, v in payload.items():
        if isinstance(v, (str, int, float, bool)) and k not in variables:
            variables[k] = str(v)

    return variables


def _shopify_automation_worker(store, topic, payload):
    """
    Worker task running in a background thread for handling Shopify automation triggers:
      - Case 1: Order Confirmation (orders/create) -> Deletes any waiting abandoned checkout
                records for the customer and immediately dispatches order confirmation email.
      - Case 2: Abandoned Checkout / Cart -> Stores a record with status="Waiting", sleeps 30 minutes.
                If no order is placed (record not deleted), dispatches abandoned notification email.
    """
    try:
        close_old_connections()
    except Exception:
        pass

    try:
        from automation.models import Automation, NotificationSend
        from automation.services import dispatch_automation_email

        norm_topic = (topic or "").lower().replace("_", "/")
        print("\n" + "=" * 80)
        print(f"[SHOPIFY AUTOMATION TASK] Processing topic: {topic} (Store: [{getattr(store, 'id', None)}] {getattr(store, 'name', '')})")
        print("=" * 80)

        variables = extract_shopify_variables(payload, topic=topic)
        customer_email = variables.get("email") or variables.get("customer_email")

        # ----------------------------------------------------------------------
        # CASE 1: ORDER CONFIRMATION / CREATION (orders/create)
        # ----------------------------------------------------------------------
        if "order" in norm_topic:
            # Step A: Delete any waiting abandoned checkout tasks for this customer
            if customer_email:
                deleted_count, _ = NotificationSend.objects.filter(
                    store=store,
                    recipient_email__iexact=customer_email,
                    status="Waiting",
                ).delete()
                if deleted_count > 0:
                    logger.info(f"[SHOPIFY AUTOMATION] Order confirmed: Removed {deleted_count} waiting abandoned checkout notification(s) for {customer_email}")
                    print(f"[SHOPIFY AUTOMATION] Order confirmed! Removed {deleted_count} waiting abandoned checkout task(s) for {customer_email}")

            if not customer_email:
                logger.warning(f"[SHOPIFY AUTOMATION] Order event has no customer email. Skipping email dispatch.")
                return

            # Step B: Find active order confirmation automation workflow
            automation = (
                Automation.objects.filter(store=store, is_active=True)
                .filter(
                    models.Q(external_name__iexact="orders/create")
                    | models.Q(external_name__iexact="order_confirmation")
                    | models.Q(external_name__icontains="order")
                    | models.Q(name__icontains="order")
                )
                .first()
            )

            if not automation:
                logger.info(f"[SHOPIFY AUTOMATION] No active order confirmation automation configured for store '{getattr(store, 'name', '')}'.")
                print(f"[SHOPIFY AUTOMATION] No active order confirmation automation found for store '{getattr(store, 'name', '')}'.")
                return

            order_id = str(payload.get("id") or payload.get("order_number") or payload.get("name") or "")
            unique_key = f"order_{order_id}" if order_id else f"order_{customer_email}"

            # Step C: Idempotency check to prevent duplicate order confirmation emails
            if NotificationSend.objects.filter(store=store, unique_key=unique_key).exists():
                logger.info(f"[SHOPIFY AUTOMATION] Order confirmation already recorded for {unique_key}. Skipping duplicate.")
                print(f"[SHOPIFY AUTOMATION] Duplicate order {unique_key} detected. Skipping.")
                return

            # Step D: Create NotificationSend record and trigger dispatch thread
            send_record = NotificationSend.objects.create(
                automation=automation,
                store=store,
                recipient_email=customer_email,
                unique_key=unique_key,
                variables=variables,
                status="Pending",
            )

            logger.info(f"[SHOPIFY AUTOMATION] Dispatched order confirmation #{send_record.id} to {customer_email}")
            print(f"[SHOPIFY AUTOMATION] Dispatched Order Confirmation #{send_record.id} to {customer_email} (Template: {automation.template_name or 'Default'})")
            dispatch_automation_email(send_record.id)

        # ----------------------------------------------------------------------
        # CASE 2: CHECKOUT / CART ABANDONED (checkouts/create, checkouts/update, carts/create)
        # ----------------------------------------------------------------------
        elif "checkout" in norm_topic or "cart" in norm_topic:
            if not customer_email:
                logger.info(f"[SHOPIFY AUTOMATION] Checkout/Cart event has no email address yet. Skipping.")
                print(f"[SHOPIFY AUTOMATION] Checkout/Cart event has no customer email yet. Waiting for email entry.")
                return

            checkout_id = str(payload.get("id") or payload.get("token") or payload.get("cart_token") or "")
            unique_key = f"checkout_{checkout_id}" if checkout_id else f"checkout_{customer_email}"

            # Don't send if already sent for this checkout
            already_sent = NotificationSend.objects.filter(
                store=store,
                recipient_email__iexact=customer_email,
                unique_key=unique_key,
                status__in=["Sent", "Opened", "Clicked"],
            ).exists()
            if already_sent:
                logger.info(f"[SHOPIFY AUTOMATION] Abandoned notification already sent for {unique_key}. Skipping.")
                return

            # Check if there is already a waiting record for this customer
            existing_waiting = NotificationSend.objects.filter(
                store=store,
                recipient_email__iexact=customer_email,
                status="Waiting",
            ).first()

            if existing_waiting:
                # Update with latest cart contents and continue waiting
                existing_waiting.variables = variables
                existing_waiting.save(update_fields=["variables", "updated_at"])
                logger.info(f"[SHOPIFY AUTOMATION] Updated existing waiting checkout record #{existing_waiting.id} for {customer_email}.")
                return

            # Find active abandoned checkout / cart automation workflow
            automation = (
                Automation.objects.filter(store=store, is_active=True)
                .filter(
                    models.Q(external_name__iexact="checkouts/create")
                    | models.Q(external_name__iexact="checkouts/update")
                    | models.Q(external_name__iexact="carts/create")
                    | models.Q(external_name__icontains="checkout")
                    | models.Q(external_name__icontains="cart")
                    | models.Q(name__icontains="abandoned")
                    | models.Q(name__icontains="checkout")
                )
                .first()
            )

            if not automation:
                logger.info(f"[SHOPIFY AUTOMATION] No active abandoned checkout automation found for store '{getattr(store, 'name', '')}'.")
                print(f"[SHOPIFY AUTOMATION] No active abandoned checkout automation configured for store '{getattr(store, 'name', '')}'.")
                return

            # Create NotificationSend record with status="Waiting"
            send_record = NotificationSend.objects.create(
                automation=automation,
                store=store,
                recipient_email=customer_email,
                unique_key=unique_key,
                variables=variables,
                status="Waiting",
            )

            record_id = send_record.id
            wait_seconds = int(os.environ.get("SHOPIFY_ABANDONED_WAIT_SECONDS", 1800))  # 30 minutes default
            logger.info(f"[SHOPIFY AUTOMATION] Created waiting record #{record_id} for {customer_email}. Waiting {wait_seconds}s for order completion.")
            print(f"[SHOPIFY AUTOMATION] Abandoned checkout recorded (#{record_id}). Waiting 30 minutes for order completion ({customer_email})...")

            # Wait 30 minutes
            time.sleep(wait_seconds)

            # Re-check database after 30 minutes
            try:
                close_old_connections()
            except Exception:
                pass

            record_after_wait = NotificationSend.objects.filter(id=record_id).first()

            if not record_after_wait:
                # Row was deleted because an order was placed within the 30-min window!
                logger.info(f"[SHOPIFY AUTOMATION] Record #{record_id} was deleted (order placed within 30 min). Abandoned email cancelled.")
                print(f"[SHOPIFY AUTOMATION] Order was placed within 30 mins! Abandoned checkout #{record_id} successfully cancelled.")
                return

            if record_after_wait.status == "Waiting":
                logger.info(f"[SHOPIFY AUTOMATION] 30 minutes elapsed without order for #{record_id}. Dispatching abandoned email to {customer_email}.")
                print(f"[SHOPIFY AUTOMATION] 30 minutes elapsed with no order for #{record_id}! Dispatching abandoned checkout notification to {customer_email}.")
                record_after_wait.status = "Pending"
                record_after_wait.save(update_fields=["status", "updated_at"])
                dispatch_automation_email(record_after_wait.id)

    except Exception as e:
        logger.error(f"[SHOPIFY AUTOMATION] Error processing event '{topic}': {e}", exc_info=True)
        print(f"[SHOPIFY AUTOMATION ERROR] {e}")
    finally:
        try:
            close_old_connections()
        except Exception:
            pass


def handle_shopify_automation_event(store, topic, payload):
    """
    Entrypoint called by the Shopify webhook receiver.
    Spawns and starts the background thread worker.
    """
    thread = threading.Thread(
        target=_shopify_automation_worker,
        args=(store, topic, payload),
        daemon=True,
    )
    thread.start()
    logger.info(
        f"[SHOPIFY AUTOMATION] Started background thread for topic '{topic}' (Store: {getattr(store, 'name', '')})"
    )
    return thread

