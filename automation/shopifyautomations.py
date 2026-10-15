import os
import json
import time
import logging
import threading
from django.db import models, close_old_connections

logger = logging.getLogger(__name__)

# Generic high-resolution shopping bag product image fallback
DEFAULT_PRODUCT_FALLBACK_IMAGE = "https://images.unsplash.com/photo-1544816155-12df9643f363?w=300&q=80"


def _fetch_shopify_product_image(title, domain):
    """
    Auto-fetches the real Shopify CDN product image from the store's public storefront.
    Shopify checkouts/create and carts/create webhooks do not include image URLs in line_items,
    so this seamlessly recovers the real image without requiring private API scopes.
    Wrapped in bulletproof try-except with a strict 1.2s timeout so it NEVER crashes or hangs.
    """
    if not domain or not title:
        return ""
    try:
        import requests
        import urllib.parse
        clean_domain = str(domain).strip().replace("https://", "").replace("http://", "").split("/")[0]
        clean_title = str(title).split(" - ")[0].split(" / ")[0].strip()
        if not clean_domain or not clean_title:
            return ""

        url = f"https://{clean_domain}/search/suggest.json?q={urllib.parse.quote(clean_title)}&resources[type]=product"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=1.2)
        if resp.status_code == 200:
            products = resp.json().get("resources", {}).get("results", {}).get("products", [])
            if products and products[0].get("image"):
                img_url = products[0]["image"]
                if img_url.startswith("//"):
                    img_url = "https:" + img_url
                return img_url
    except Exception as exc:
        logger.debug(f"[AUTO PRODUCT IMAGE] Could not fetch image for '{title}' on {domain}: {exc}")
    return ""


def extract_shopify_variables(payload, topic="", store=None):
    """
    Standardizes and flattens variables from Shopify webhook payloads
    so they can be dynamically replaced as {{ variable_name }} in email templates.
    Fully protected with defensive try-except blocks so it NEVER raises or aborts notification creation.
    """
    if not isinstance(payload, dict):
        payload = {}

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
    shop_domain = ""
    if abandoned_url:
        try:
            from urllib.parse import urlparse
            parsed_url = urlparse(abandoned_url if "://" in abandoned_url else f"https://{abandoned_url}")
            shop_domain = parsed_url.netloc or parsed_url.path.split("/")[0]
        except Exception:
            pass
    if not shop_domain and store:
        try:
            from shopify_integration.models import ShopifySettings
            settings_obj = ShopifySettings.objects.filter(store=store).first()
            if settings_obj and settings_obj.shop_url:
                shop_domain = settings_obj.shop_url
        except Exception:
            pass

    # Line items extraction and dynamic HTML generator
    line_items_data = []
    line_items = payload.get("line_items") or []
    item_titles = []

    if isinstance(line_items, list):
        for idx, it in enumerate(line_items):
            try:
                if not isinstance(it, dict):
                    continue
                title = it.get("title") or "Selected Product"
                item_titles.append(title)
                variant = it.get("variant_title") or ""
                qty = str(it.get("quantity") or 1)
                price_val = str(it.get("price") or "0.00")

                # Resolve image
                img = ""
                try:
                    img = (
                        it.get("image_url")
                        or (it.get("image") if isinstance(it.get("image"), str) else (it.get("image") or {}).get("src"))
                        or (it.get("featured_image") or {}).get("url")
                        or ""
                    )
                    if not img and shop_domain and title:
                        img = _fetch_shopify_product_image(title, shop_domain)
                except Exception as img_err:
                    logger.debug(f"[AUTO PRODUCT IMAGE] Error resolving item image: {img_err}")
                    img = ""

                if not img:
                    img = DEFAULT_PRODUCT_FALLBACK_IMAGE

                is_even = (idx % 2 == 1)

                line_items_data.append({
                    "title": title,
                    "variant": variant,
                    "quantity": qty,
                    "price": price_val,
                    "formatted_price": f"{currency} {price_val}",
                    "image": img,
                    "index": idx + 1,
                    "is_even": is_even,
                })
            except Exception as line_err:
                logger.warning(f"[SHOPIFY VARIABLES] Error parsing line item {idx}: {line_err}")
                continue

    items_summary = ", ".join(item_titles) if item_titles else "Your selected items"

    # Explicit product browse or first item extraction
    first_item = line_items_data[0] if line_items_data else {}
    p_title = str(payload.get("product_title") or first_item.get("title") or "Selected Product")
    p_img = str(payload.get("product_image") or first_item.get("image") or DEFAULT_PRODUCT_FALLBACK_IMAGE)
    p_price = str(payload.get("product_price") or first_item.get("price") or "0.00")
    p_url = str(payload.get("product_url") or abandoned_url or "")

    variables = {
        "product_title": p_title,
        "product_image": p_img,
        "product_price": p_price,
        "product_url": p_url,
        "item_title": p_title,
        "item_image": p_img,
        "item_price": p_price,

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
        "cancel_reason": str(payload.get("cancel_reason") or "customer"),
        "cancelled_at": str(payload.get("cancelled_at") or ""),
        "fulfillment_status": str(payload.get("fulfillment_status") or "unfulfilled"),
        "shipping_address": shipping_street,
        "shipping_city": shipping_city,
        "shipping_zip": shipping_zip,
        "shipping_country": shipping_country,
        "abandoned_checkout_url": abandoned_url,
        "recovery_url": abandoned_url,
        "checkout_url": abandoned_url,
        "items_summary": items_summary,
        "items_count": str(len(line_items_data)),
        "line_items_data": line_items_data,
    }

    # Pass all top-level primitives from Shopify payload as available variables
    try:
        for k, v in payload.items():
            if isinstance(v, (str, int, float, bool)) and k not in variables:
                variables[k] = str(v)
    except Exception:
        pass

    return variables


def _shopify_automation_worker(store, topic, payload):
    """
    Worker task running in a background thread for handling Shopify automation triggers:
      - Case 1: Order Cancellation (orders/cancelled) -> Dispatches order cancellation email.
      - Case 2: Order Confirmation (orders/create) -> Clears waiting abandoned checkout/browse tasks
                and immediately dispatches order confirmation email.
      - Case 3: Abandoned Checkout / Cart -> Stores a record with status="Waiting".
      - Case 4: Browse Abandonment -> Stores a record with status="Waiting".
    """
    try:
        close_old_connections()
    except Exception:
        pass

    try:
        from automation.models import Automation, NotificationSend
        from automation.services import dispatch_automation_email

        if not isinstance(payload, dict):
            logger.error(f"[SHOPIFY AUTOMATION] Invalid payload type: {type(payload)}")
            return

        norm_topic = (topic or "").lower().replace("_", "/")
        print("=" * 80)
        print(f"[SHOPIFY AUTOMATION TASK] Processing topic: {topic} (Store: [{getattr(store, 'id', None)}] {getattr(store, 'name', '')})")
        print("=" * 80)

        try:
            variables = extract_shopify_variables(payload, topic=topic, store=store)
        except Exception as var_exc:
            logger.error(f"[SHOPIFY AUTOMATION] Error extracting variables: {var_exc}", exc_info=True)
            variables = {
                "email": payload.get("email") or payload.get("contact_email") or (payload.get("customer") or {}).get("email") or "",
                "product_image": DEFAULT_PRODUCT_FALLBACK_IMAGE,
                "item_image": DEFAULT_PRODUCT_FALLBACK_IMAGE,
                "line_items_data": [],
            }

        customer_email = (
            variables.get("email")
            or variables.get("customer_email")
            or payload.get("email")
            or payload.get("contact_email")
            or (payload.get("customer") or {}).get("email")
            or ""
        ).strip()

        # ----------------------------------------------------------------------
        # CASE 1: ORDER CANCELLATION (orders/cancelled)
        # ----------------------------------------------------------------------
        if "cancel" in norm_topic:
            if not customer_email:
                logger.warning(f"[SHOPIFY AUTOMATION] Order cancellation event has no customer email. Skipping email dispatch.")
                return

            automation = Automation.objects.filter(
                store=store, is_active=True, external_name="orders/cancelled"
            ).first()

            if not automation:
                logger.info(f"[SHOPIFY AUTOMATION] No active order cancellation automation configured for store '{getattr(store, 'name', '')}'.")
                print(f"[SHOPIFY AUTOMATION] No active order cancellation automation found for store '{getattr(store, 'name', '')}'.")
                return

            order_id = str(payload.get("id") or payload.get("order_number") or payload.get("name") or "")
            unique_key = f"order_cancelled_{order_id}" if order_id else f"order_cancelled_{customer_email}"

            if NotificationSend.objects.filter(store=store, automation=automation, unique_key=unique_key).exists():
                logger.info(f"[SHOPIFY AUTOMATION] Order cancellation already recorded for {unique_key}. Skipping duplicate.")
                print(f"[SHOPIFY AUTOMATION] Duplicate order cancellation {unique_key} detected. Skipping.")
                return

            try:
                send_record = NotificationSend.objects.create(
                    automation=automation,
                    store=store,
                    recipient_email=customer_email,
                    unique_key=unique_key,
                    variables=variables,
                    status="Pending",
                )
                logger.info(f"[SHOPIFY AUTOMATION] Dispatched order cancellation #{send_record.id} to {customer_email}")
                print(f"[SHOPIFY AUTOMATION] Dispatched Order Cancellation #{send_record.id} to {customer_email} (Template: {automation.template_name or 'Default'})")
                dispatch_automation_email(send_record.id)
            except Exception as create_exc:
                logger.error(f"[SHOPIFY AUTOMATION] Failed creating order cancellation notification: {create_exc}", exc_info=True)

        # ----------------------------------------------------------------------
        # CASE 2: ORDER CONFIRMATION / CREATION (orders/create)
        # ----------------------------------------------------------------------
        elif "order" in norm_topic:
            # Step A: Delete any waiting abandoned checkout/browse tasks for this customer
            if customer_email:
                try:
                    deleted_count, _ = NotificationSend.objects.filter(
                        store=store,
                        automation__external_name__in=["checkouts/create", "carts/create", "browse_abandonment", "browse/abandonment"],
                        recipient_email__iexact=customer_email,
                        status="Waiting",
                    ).delete()
                    if deleted_count > 0:
                        logger.info(f"[SHOPIFY AUTOMATION] Order confirmed: Removed {deleted_count} waiting abandoned checkout notification(s) for {customer_email}")
                        print(f"[SHOPIFY AUTOMATION] Order confirmed! Removed {deleted_count} waiting abandoned checkout task(s) for {customer_email}")
                except Exception as del_err:
                    logger.warning(f"[SHOPIFY AUTOMATION] Error clearing waiting notifications on order: {del_err}")

            # Step A2: Attribution check for Recovered Abandoned Checkout
            if customer_email:
                try:
                    from django.utils import timezone
                    now_str = timezone.now().isoformat()
                    order_num = str(payload.get("order_number") or payload.get("name") or "")
                    order_amt = str(payload.get("total_price") or payload.get("current_total_price") or "0.00")
                    order_curr = str(payload.get("currency") or payload.get("presentment_currency") or "PKR")
                    checkout_id_raw = str(payload.get("checkout_id") or payload.get("cart_token") or "")

                    abandoned_q = models.Q(
                        store=store,
                        recipient_email__iexact=customer_email,
                        status__in=["Sent", "Opened", "Clicked"],
                    )
                    if checkout_id_raw:
                        abandoned_q |= models.Q(
                            store=store,
                            unique_key__icontains=checkout_id_raw,
                            status__in=["Sent", "Opened", "Clicked"],
                        )

                    sent_abandoned = (
                        NotificationSend.objects.filter(abandoned_q)
                        .filter(automation__external_name__in=["checkouts/create", "carts/create"])
                        .order_by("-created_at")
                        .first()
                    )

                    if sent_abandoned:
                        rec_vars = dict(sent_abandoned.variables or {})
                        rec_vars["recovered_order_name"] = str(payload.get("name") or (f"#{order_num}" if order_num else ""))
                        rec_vars["recovered_order_id"] = str(payload.get("id") or order_num)
                        rec_vars["recovered_amount"] = order_amt
                        rec_vars["recovered_currency"] = order_curr
                        rec_vars["recovered_at"] = now_str

                        sent_abandoned.status = "Recovered"
                        sent_abandoned.variables = rec_vars
                        sent_abandoned.save(update_fields=["status", "variables", "updated_at"])
                        logger.info(
                            f"[SHOPIFY AUTOMATION] 💰 RECOVERED CART! Marked #{sent_abandoned.id} as Recovered for {customer_email}. (Order: {rec_vars['recovered_order_name']}, Amount: {order_curr} {order_amt})"
                        )
                        print(
                            f"[SHOPIFY AUTOMATION] 💰 RECOVERED CART! Marked #{sent_abandoned.id} as Recovered for {customer_email}. (Order: {rec_vars['recovered_order_name']}, Amount: {order_curr} {order_amt})"
                        )
                except Exception as rec_err:
                    logger.warning(f"[SHOPIFY AUTOMATION] Error recording cart recovery attribution: {rec_err}")

            if not customer_email:
                logger.warning(f"[SHOPIFY AUTOMATION] Order event has no customer email. Skipping email dispatch.")
                return

            # Step B: Find active order confirmation automation by exact external_name
            automation = Automation.objects.filter(
                store=store, is_active=True, external_name="orders/create"
            ).first()

            if not automation:
                logger.info(f"[SHOPIFY AUTOMATION] No active order confirmation automation configured for store '{getattr(store, 'name', '')}'.")
                print(f"[SHOPIFY AUTOMATION] No active order confirmation automation found for store '{getattr(store, 'name', '')}'.")
                return

            order_id = str(payload.get("id") or payload.get("order_number") or payload.get("name") or "")
            unique_key = f"order_{order_id}" if order_id else f"order_{customer_email}"

            # Step C: Idempotency check to prevent duplicate order confirmation emails
            if NotificationSend.objects.filter(store=store, automation=automation, unique_key=unique_key).exists():
                logger.info(f"[SHOPIFY AUTOMATION] Order confirmation already recorded for {unique_key}. Skipping duplicate.")
                print(f"[SHOPIFY AUTOMATION] Duplicate order {unique_key} detected. Skipping.")
                return

            # Step D: Create NotificationSend record and trigger dispatch thread
            try:
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
            except Exception as create_exc:
                logger.error(f"[SHOPIFY AUTOMATION] Failed creating order confirmation notification: {create_exc}", exc_info=True)

        # ----------------------------------------------------------------------
        # CASE 3: CHECKOUT / CART ABANDONED (checkouts/create, carts/create)
        # ----------------------------------------------------------------------
        elif "checkout" in norm_topic or "cart" in norm_topic:
            if not customer_email:
                logger.info(f"[SHOPIFY AUTOMATION] Checkout/Cart event has no email address yet. Skipping.")
                return

            automation = Automation.objects.filter(
                store=store, is_active=True, external_name__in=["checkouts/create", "carts/create"]
            ).first()

            if not automation:
                logger.info(f"[SHOPIFY AUTOMATION] No active abandoned checkout automation found for store '{getattr(store, 'name', '')}'.")
                return

            checkout_id = str(payload.get("id") or payload.get("token") or payload.get("cart_token") or "")
            unique_key = f"checkout_{checkout_id}" if checkout_id else f"checkout_{customer_email}"

            already_sent = NotificationSend.objects.filter(
                store=store,
                automation=automation,
                recipient_email__iexact=customer_email,
                unique_key=unique_key,
                status__in=["Sent", "Opened", "Clicked", "InProgress"],
            ).exists()
            if already_sent:
                logger.info(f"[SHOPIFY AUTOMATION] Abandoned notification already sent/processing for {unique_key}. Skipping.")
                return

            # Match waiting notification ONLY for this specific automation & unique_key
            existing_waiting = NotificationSend.objects.filter(
                store=store,
                automation=automation,
                recipient_email__iexact=customer_email,
                unique_key=unique_key,
                status="Waiting",
            ).first()

            if existing_waiting:
                existing_waiting.variables = variables
                existing_waiting.save(update_fields=["variables", "updated_at"])
                logger.info(f"[SHOPIFY AUTOMATION] Updated existing waiting checkout record #{existing_waiting.id} for {customer_email}.")
                return

            try:
                send_record = NotificationSend.objects.create(
                    automation=automation,
                    store=store,
                    recipient_email=customer_email,
                    unique_key=unique_key,
                    variables=variables,
                    status="Waiting",
                )
                logger.info(f"[SHOPIFY AUTOMATION] Abandoned checkout recorded #{send_record.id} for {customer_email}. Queued in Waiting status.")
                print(f"[SHOPIFY AUTOMATION] Abandoned checkout recorded (#{send_record.id}) for {customer_email}. Queued in Waiting status (No sleep).")
            except Exception as create_exc:
                logger.error(f"[SHOPIFY AUTOMATION] Failed creating abandoned checkout notification: {create_exc}", exc_info=True)
            return

        # ----------------------------------------------------------------------
        # CASE 4: BROWSE ABANDONMENT (browse/abandonment, browse)
        # ----------------------------------------------------------------------
        elif "browse" in norm_topic:
            if not customer_email:
                logger.info(f"[SHOPIFY AUTOMATION] Browse event has no email address. Skipping.")
                return

            automation = Automation.objects.filter(
                store=store, is_active=True, external_name__in=["browse_abandonment", "browse/abandonment"]
            ).first()

            if not automation:
                logger.info(f"[SHOPIFY AUTOMATION] No active browse abandonment automation found for store '{getattr(store, 'name', '')}'.")
                return

            product_id = str(payload.get("product_id") or "")
            unique_key = f"browse_{product_id}_{customer_email}" if product_id else f"browse_{customer_email}"

            already_sent = NotificationSend.objects.filter(
                store=store,
                automation=automation,
                recipient_email__iexact=customer_email,
                unique_key=unique_key,
                status__in=["Sent", "Opened", "Clicked", "InProgress"],
            ).exists()
            if already_sent:
                return

            existing_waiting = NotificationSend.objects.filter(
                store=store,
                automation=automation,
                recipient_email__iexact=customer_email,
                unique_key=unique_key,
                status="Waiting",
            ).first()

            if existing_waiting:
                existing_waiting.variables = variables
                existing_waiting.save(update_fields=["variables", "updated_at"])
                logger.info(f"[SHOPIFY AUTOMATION] Updated existing waiting browse record #{existing_waiting.id} for {customer_email}.")
                return

            try:
                send_record = NotificationSend.objects.create(
                    automation=automation,
                    store=store,
                    recipient_email=customer_email,
                    unique_key=unique_key,
                    variables=variables,
                    status="Waiting",
                )
                logger.info(f"[SHOPIFY AUTOMATION] Browse abandonment recorded #{send_record.id} for {customer_email}. Queued in Waiting status.")
                print(f"[SHOPIFY AUTOMATION] Browse abandonment recorded (#{send_record.id}) for {customer_email}. Queued in Waiting status (No sleep).")
            except Exception as create_exc:
                logger.error(f"[SHOPIFY AUTOMATION] Failed creating browse abandonment notification: {create_exc}", exc_info=True)
            return

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
