import threading
import hmac
import hashlib
import base64
import json
import logging
from django.conf import settings as settings_conf
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

from EmailMarketing.Views.base import StoreAuthenticatedMixin
from Accounts.models import Store, Contact
from EmailMarketing.models import EmailSegment
from shopify_integration.models import ShopifySettings
from automation.shopifyautomations import handle_shopify_automation_event
from shopify_integration.sync import (
    fetch_segment_member_emails,
    get_valid_shopify_token,
    shopify_api_request,
)

logger = logging.getLogger(__name__)

SHOPIFY_WEBHOOK_TOPICS = [
    "CUSTOMERS_CREATE",
    "CUSTOMERS_UPDATE",
    "SEGMENTS_CREATE",
    "SEGMENTS_UPDATE",
    "SEGMENTS_DELETE"
]


def register_shopify_webhooks(store, custom_callback_url=None):
    """
    Uses Shopify GraphQL API to register or reset webhook subscriptions
    for customer and segment events.
    1. Lists existing webhook subscriptions for the store.
    2. Deletes outdated subscriptions.
    3. Creates fresh webhook subscriptions via GraphQL webhookSubscriptionCreate mutation.
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shopify_access_token:
        logger.error(f"[SHOPIFY WEBHOOK REGISTER] No Shopify settings found for store {store}")
        return False, "No Shopify integration configured for this store."

    active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token
    shop_url = settings_obj.shop_url
    gql_url = f"https://{shop_url}/admin/api/2023-04/graphql.json"

    backend_base = getattr(settings_conf, "BACKEND_BASE_URL", "").rstrip("/")
    if not backend_base:
        backend_base = "https://marketing-be.technogroves.com"
    
    callback_url = custom_callback_url or f"{backend_base}/shopify/webhooks/receiver/"

    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    list_query = """
    {
      webhookSubscriptions(first: 100) {
        edges {
          node {
            id
            topic
            endpoint {
              __typename
              ... on WebhookHttpEndpoint {
                callbackUrl
              }
            }
          }
        }
      }
    }
    """

    try:
        resp = shopify_api_request("post", gql_url, store, headers=headers, json={"query": list_query})
        if resp.status_code == 200:
            existing_data = resp.json().get("data", {}).get("webhookSubscriptions", {}).get("edges", [])
            for edge in existing_data:
                sub_node = edge.get("node", {})
                sub_id = sub_node.get("id")
                topic = sub_node.get("topic")
                
                if topic in SHOPIFY_WEBHOOK_TOPICS and sub_id:
                    delete_mutation = """
                    mutation webhookSubscriptionDelete($id: ID!) {
                      webhookSubscriptionDelete(id: $id) {
                        deletedWebhookSubscriptionId
                        userErrors {
                          field
                          message
                        }
                      }
                    }
                    """
                    delete_response = shopify_api_request(
                        "post",
                        gql_url,
                        store,
                        headers=headers,
                        json={"query": delete_mutation, "variables": {"id": sub_id}},
                    )
                    if delete_response.status_code == 200:
                        logger.info(f"[SHOPIFY WEBHOOK REGISTER] Deleted old webhook {sub_id} for topic {topic}")
                    else:
                        logger.error(
                            f"[SHOPIFY WEBHOOK REGISTER] Failed deleting webhook {sub_id}: "
                            f"HTTP {delete_response.status_code}"
                        )

        else:
            logger.error(
                f"[SHOPIFY WEBHOOK REGISTER] Failed listing webhooks: HTTP {resp.status_code}"
            )

    except Exception as list_err:
        logger.warning(f"[SHOPIFY WEBHOOK REGISTER] Listing existing webhooks warning: {list_err}")

    registered = []
    errors = []

    create_mutation = """
    mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
      webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
        userErrors {
          field
          message
        }
        webhookSubscription {
          id
          topic
        }
      }
    }
    """

    for topic in SHOPIFY_WEBHOOK_TOPICS:
        variables = {
            "topic": topic,
            "webhookSubscription": {
                "callbackUrl": callback_url,
                "format": "JSON"
            }
        }

        try:
            res = shopify_api_request(
                "post",
                gql_url,
                store,
                headers=headers,
                json={"query": create_mutation, "variables": variables},
            )
            if res.status_code == 200:
                res_json = res.json()
                user_errors = res_json.get("data", {}).get("webhookSubscriptionCreate", {}).get("userErrors", [])
                sub_info = res_json.get("data", {}).get("webhookSubscriptionCreate", {}).get("webhookSubscription")
                
                if user_errors:
                    err_msg = f"Topic {topic}: {user_errors[0].get('message')}"
                    logger.warning(f"[SHOPIFY WEBHOOK REGISTER] {err_msg}")
                    errors.append(err_msg)
                elif sub_info:
                    logger.info(f"[SHOPIFY WEBHOOK REGISTER] Subscribed topic {topic} -> {callback_url}")
                    registered.append(topic)
            else:
                errors.append(f"Topic {topic} HTTP {res.status_code}")
        except Exception as sub_err:
            errors.append(f"Topic {topic}: {str(sub_err)}")

    if registered:
        return True, f"Successfully registered {len(registered)} webhooks via Shopify GraphQL API.", registered, callback_url
    else:
        return False, f"Failed to register webhooks. Errors: {', '.join(errors)}", [], callback_url


class ShopifyWebhookRegisterView(StoreAuthenticatedMixin, APIView):
    """
    POST /shopify/webhooks/register/
    Triggers GraphQL registration/reset of all customer and segment webhooks for active store.
    """
    def post(self, request):
        custom_url = request.data.get("callback_url")
        success, msg, topics, final_url = register_shopify_webhooks(request.store, custom_callback_url=custom_url)
        
        if success:
            return Response({
                "message": msg,
                "registered_topics": topics,
                "callback_url": final_url
            })
        else:
            return Response({
                "error": msg,
                "registered_topics": topics,
                "callback_url": final_url
            }, status=status.HTTP_400_BAD_REQUEST)


class ShopifyWebhookReceiverView(APIView):
    """
    POST /shopify/webhooks/receiver/
    Central receiver for real-time Shopify webhook events:
      - customers/create (CUSTOMERS_CREATE)
      - customers/update (CUSTOMERS_UPDATE)
      - segments/create  (SEGMENTS_CREATE)
      - segments/update  (SEGMENTS_UPDATE)
      - segments/delete  (SEGMENTS_DELETE)
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        topic = request.headers.get("X-Shopify-Topic", "").upper().replace("/", "_")
        shop_domain = request.headers.get("X-Shopify-Shop-Domain")
        shopify_hmac = request.headers.get("X-Shopify-Hmac-Sha256")

        logger.info(f"[SHOPIFY WEBHOOK RECEIVER] Incoming topic '{topic}' from shop '{shop_domain}'")

        if not shop_domain:
            return HttpResponse("Missing shop domain header", status=400)

        clean_shop = shop_domain.replace("https://", "").replace("http://", "").strip("/").split("/")[0].lower()

        settings_obj = (
            ShopifySettings.objects.filter(shop_url__icontains=clean_shop).first() or
            ShopifySettings.objects.filter(shop_url=shop_domain).first()
        )
        if not settings_obj:
            logger.warning(f"[SHOPIFY WEBHOOK RECEIVER] No store found for domain {shop_domain} (clean: {clean_shop})")
            return HttpResponse("Shop domain not registered", status=404)

        store = settings_obj.store
        api_secret = settings_obj.get_api_secret() if settings_obj else getattr(settings_conf, "SHOPIFY_API_SECRET", "")

        if api_secret and shopify_hmac:
            raw_body = request.body
            calculated_hmac = base64.b64encode(
                hmac.new(api_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
            ).decode("utf-8")

            if not hmac.compare_digest(calculated_hmac, shopify_hmac):
                logger.error("[SHOPIFY WEBHOOK RECEIVER] HMAC verification failed!")
                return HttpResponse("Invalid HMAC signature", status=401)

        try:
            payload = json.loads(request.body.decode("utf-8")) if request.body else {}
        except Exception as e:
            logger.error(f"[SHOPIFY WEBHOOK RECEIVER] Failed to parse JSON payload: {e}")
            return HttpResponse("Invalid JSON payload", status=400)

        if "CUSTOMER" in topic:
            email = payload.get("email")
            if email:
                first_name = payload.get("first_name", "") or ""
                last_name = payload.get("last_name", "") or ""
                phone = payload.get("phone", "") or ""
                external_id = str(payload.get("id", ""))
                orders_count = payload.get("orders_count", 0)
                total_spent = payload.get("total_spent", 0.0)

                accepts_marketing = payload.get("accepts_marketing", False)
                email_consent = payload.get("email_marketing_consent") or {}
                if isinstance(email_consent, dict) and email_consent.get("state"):
                    accepts_marketing = (email_consent.get("state") == "subscribed")

                default_address = payload.get("default_address", {}) or {}
                city = default_address.get("city", "") or ""
                country = default_address.get("country", "") or ""

                contact, created = Contact.objects.update_or_create(
                    store=store,
                    email=email,
                    defaults={
                        "first_name": first_name,
                        "last_name": last_name,
                        "phone": phone,
                        "external_id": external_id,
                        "accept_email_marketing": accepts_marketing,
                        "total_orders": orders_count,
                        "total_spent": total_spent,
                        "city": city,
                        "country": country,
                        "raw_data": payload,
                    }
                )
                action = "created" if created else "updated"
                logger.info(f"[SHOPIFY WEBHOOK] Customer {email} {action} in store {store.name}")

        elif "SEGMENT" in topic and "DELETE" not in topic:
            shopify_id = payload.get("id")
            name = payload.get("name")
            shopify_query = payload.get("query", "")

            if name and shopify_id:
                active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token
                member_emails = fetch_segment_member_emails(
                    shop_domain, 
                    active_token, 
                    shopify_id=shopify_id, 
                    shopify_query=shopify_query, 
                    store=store
                )

                segment, _ = EmailSegment.objects.update_or_create(
                    store=store,
                    name=name,
                    defaults={
                        "description": f"Shopify Segment Query: {shopify_query}",
                        "cached_contact_count": len(member_emails),
                        "filter_config": {
                            "shopify_id": shopify_id,
                            "shopify_query": shopify_query,
                            "member_emails": member_emails,
                        },
                    }
                )

                matching_contacts = Contact.objects.filter(store=store, email__in=member_emails)
                segment.contacts.set(matching_contacts)
                logger.info(f"[SHOPIFY WEBHOOK] Segment '{name}' synced with {matching_contacts.count()} member contacts")

        elif "SEGMENT_DELETE" in topic or "SEGMENTS_DELETE" in topic:
            shopify_id = payload.get("id")
            name = payload.get("name")
            if shopify_id or name:
                deleted_count, _ = EmailSegment.objects.filter(store=store, name=name).delete()
                logger.info(f"[SHOPIFY WEBHOOK] Deleted segment '{name}' ({deleted_count} records removed)")

        elif any(k in topic for k in ["ORDER", "CHECKOUT", "CART"]):
            try:
                raw_topic = request.headers.get("X-Shopify-Topic", topic)
                handle_shopify_automation_event(store, raw_topic, payload)
                logger.info(f"[SHOPIFY WEBHOOK] Dispatched topic '{raw_topic}' to automation handler (Store: {store.name})")
            except Exception as e:
                logger.error(f"[SHOPIFY WEBHOOK ERROR] Error handling automation event: {e}", exc_info=True)
            return HttpResponse("Shopify automation webhook queued successfully", status=200)

        return HttpResponse("Webhook processed successfully", status=200)


# ==============================================================================
# AUTOMATION WEBHOOK MANAGEMENT (Orders, Checkouts, Carts)
# ==============================================================================

AUTOMATION_WEBHOOK_GROUPS = {
    "orders": ["ORDERS_CREATE", "ORDERS_CANCELLED"],
    "checkouts": ["CHECKOUTS_CREATE", "CHECKOUTS_UPDATE"],
    "carts": ["CARTS_CREATE", "CARTS_UPDATE"],
}


def get_shopify_webhook_subscriptions(store):
    """
    Queries Shopify GraphQL API to list all active webhook subscriptions for this store.
    Returns: list of dicts: [{'id': ..., 'topic': ..., 'callback_url': ...}]
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shopify_access_token:
        return []

    active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token
    shop_url = settings_obj.shop_url
    gql_url = f"https://{shop_url}/admin/api/2023-04/graphql.json"

    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    list_query = """
    {
      webhookSubscriptions(first: 100) {
        edges {
          node {
            id
            topic
            endpoint {
              __typename
              ... on WebhookHttpEndpoint {
                callbackUrl
              }
            }
          }
        }
      }
    }
    """
    try:
        resp = shopify_api_request("post", gql_url, store, headers=headers, json={"query": list_query})
        if resp.status_code == 200:
            edges = resp.json().get("data", {}).get("webhookSubscriptions", {}).get("edges", [])
            subs = []
            for edge in edges:
                node = edge.get("node", {})
                endpoint = node.get("endpoint") or {}
                subs.append({
                    "id": node.get("id"),
                    "topic": node.get("topic"),
                    "callback_url": endpoint.get("callbackUrl") if isinstance(endpoint, dict) else None,
                })
            return subs
    except Exception as e:
        logger.error(f"[SHOPIFY WEBHOOK LIST] Error listing webhooks for store {store.id}: {e}")
    return []


def get_shopify_webhook_status_summary(store):
    """
    Returns boolean status for each webhook group:
    {
        'customer': True/False,
        'orders': True/False,
        'checkouts': True/False,
        'carts': True/False,
        'active_topics': [...]
    }
    """
    subs = get_shopify_webhook_subscriptions(store)
    active_topics = set(s.get("topic") for s in subs if s.get("topic"))

    customer_topics = set(SHOPIFY_WEBHOOK_TOPICS)
    order_topics = set(AUTOMATION_WEBHOOK_GROUPS["orders"])
    checkout_topics = set(AUTOMATION_WEBHOOK_GROUPS["checkouts"])
    cart_topics = set(AUTOMATION_WEBHOOK_GROUPS["carts"])

    return {
        "customer": bool(customer_topics.intersection(active_topics)),
        "orders": bool(order_topics.intersection(active_topics)),
        "checkouts": bool(checkout_topics.intersection(active_topics)),
        "carts": bool(cart_topics.intersection(active_topics)),
        "active_topics": list(active_topics),
    }


def manage_shopify_automation_webhooks(store, webhook_type, action="toggle", custom_callback_url=None):
    """
    Subscribes or unsubscribes Shopify webhook groups:
    - 'orders': ORDERS_CREATE
    - 'checkouts': CHECKOUTS_CREATE, CHECKOUTS_UPDATE
    - 'carts': CARTS_CREATE, CARTS_UPDATE
    
    action:
    - 'subscribe': ensure topics are subscribed
    - 'unsubscribe': delete subscriptions for these topics
    - 'toggle': if any subscribed, unsubscribe them; if none subscribed, subscribe them.
    """
    webhook_type = (webhook_type or "").lower().strip()
    if webhook_type in ["order", "orders"]:
        group_key = "orders"
    elif webhook_type in ["checkout", "checkouts"]:
        group_key = "checkouts"
    elif webhook_type in ["cart", "carts"]:
        group_key = "carts"
    else:
        return False, f"Unsupported webhook type '{webhook_type}'. Supported: orders, checkouts, carts.", "error"

    target_topics = AUTOMATION_WEBHOOK_GROUPS.get(group_key, [])
    if not target_topics:
        return False, f"No topics configured for type '{webhook_type}'.", "error"

    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shopify_access_token:
        return False, "No Shopify integration configured for this store.", "error"

    active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token
    shop_url = settings_obj.shop_url
    gql_url = f"https://{shop_url}/admin/api/2023-04/graphql.json"

    backend_base = getattr(settings_conf, "BACKEND_BASE_URL", "").rstrip("/")
    if not backend_base:
        backend_base = "https://marketing-be.technogroves.com"
    callback_url = custom_callback_url or f"{backend_base}/shopify/webhooks/receiver/"

    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    # Fetch existing subscriptions
    existing_subs = get_shopify_webhook_subscriptions(store)
    existing_by_topic = {s["topic"]: s["id"] for s in existing_subs if s.get("topic") in target_topics and s.get("id")}

    is_currently_subscribed = len(existing_by_topic) > 0

    # Determine desired action
    should_unsubscribe = False
    if action == "unsubscribe":
        should_unsubscribe = True
    elif action == "subscribe":
        should_unsubscribe = False
    else:  # toggle
        should_unsubscribe = is_currently_subscribed

    delete_mutation = """
    mutation webhookSubscriptionDelete($id: ID!) {
      webhookSubscriptionDelete(id: $id) {
        deletedWebhookSubscriptionId
        userErrors {
          field
          message
        }
      }
    }
    """

    create_mutation = """
    mutation webhookSubscriptionCreate($topic: WebhookSubscriptionTopic!, $webhookSubscription: WebhookSubscriptionInput!) {
      webhookSubscriptionCreate(topic: $topic, webhookSubscription: $webhookSubscription) {
        userErrors {
          field
          message
        }
        webhookSubscription {
          id
          topic
        }
      }
    }
    """

    if should_unsubscribe:
        deleted_topics = []
        errors = []
        for topic, sub_id in existing_by_topic.items():
            del_resp = shopify_api_request("post", gql_url, store, headers=headers, json={
                "query": delete_mutation,
                "variables": {"id": sub_id}
            })
            if del_resp.status_code == 200:
                user_errors = del_resp.json().get("data", {}).get("webhookSubscriptionDelete", {}).get("userErrors", [])
                if user_errors:
                    errors.append(f"{topic}: {user_errors[0].get('message')}")
                else:
                    deleted_topics.append(topic)
            else:
                errors.append(f"{topic} HTTP {del_resp.status_code}")

        if errors and not deleted_topics:
            return False, f"Failed to unsubscribe {group_key} webhooks: {', '.join(errors)}", "unsubscribed"

        return True, f"Successfully unsubscribed {group_key.capitalize()} webhooks ({', '.join(deleted_topics or target_topics)}).", "unsubscribed"

    else:
        registered_topics = []
        errors = []
        for topic in target_topics:
            if topic in existing_by_topic:
                registered_topics.append(topic)
                continue

            variables = {
                "topic": topic,
                "webhookSubscription": {
                    "callbackUrl": callback_url,
                    "format": "JSON"
                }
            }
            res = shopify_api_request("post", gql_url, store, headers=headers, json={
                "query": create_mutation,
                "variables": variables
            })
            if res.status_code == 200:
                res_json = res.json()
                user_errors = res_json.get("data", {}).get("webhookSubscriptionCreate", {}).get("userErrors", [])
                sub_info = res_json.get("data", {}).get("webhookSubscriptionCreate", {}).get("webhookSubscription")
                if user_errors:
                    raw_msg = user_errors[0].get("message", "")
                    if "cannot create a webhook subscription with the specified topic" in raw_msg.lower():
                        err_msg = (
                            f"Missing Shopify Access Scope: Your Shopify App configuration is missing the required scopes for {topic}. "
                            f"Please update your app scopes in your Shopify Partner Dashboard / Store Settings (e.g. read_orders, read_checkouts) "
                            f"and re-authorize via 'OAuth Install & Connect'."
                        )
                    else:
                        err_msg = f"{topic}: {raw_msg}"
                    errors.append(err_msg)
                elif sub_info:
                    registered_topics.append(topic)
            else:
                errors.append(f"{topic} HTTP {res.status_code}")

        if not registered_topics and errors:
            return False, f"Failed to subscribe {group_key} webhooks: {', '.join(errors)}", "subscribed"

        msg = f"Successfully subscribed {group_key.capitalize()} webhooks ({', '.join(registered_topics)})."
        if errors:
            msg += f" Note: {', '.join(errors)}"
        return True, msg, "subscribed"


class ShopifyWebhookManageView(StoreAuthenticatedMixin, APIView):
    """
    API for managing automation webhooks (Orders, Checkouts, Carts).
    
    GET /shopify/webhooks/manage/
    Returns current status of webhook subscriptions for the store.
    
    POST /shopify/webhooks/manage/
    Body params:
      - type: 'orders' | 'checkouts' | 'carts'
      - action: 'subscribe' | 'unsubscribe' | 'toggle' (default: 'toggle')
    """
    def get(self, request):
        status_summary = get_shopify_webhook_status_summary(request.store)
        return Response({
            "status": "ok",
            "subscriptions": status_summary
        })

    def post(self, request):
        webhook_type = request.data.get("type")
        action = request.data.get("action", "toggle")
        custom_url = request.data.get("callback_url")

        if not webhook_type:
            return Response(
                {"error": "Field 'type' is required (supported: 'orders', 'checkouts', 'carts')."},
                status=status.HTTP_400_BAD_REQUEST
            )

        success, message, action_performed = manage_shopify_automation_webhooks(
            request.store,
            webhook_type=webhook_type,
            action=action,
            custom_callback_url=custom_url
        )

        updated_status = get_shopify_webhook_status_summary(request.store)

        if success:
            return Response({
                "success": True,
                "message": message,
                "action": action_performed,
                "type": webhook_type,
                "subscriptions": updated_status
            })
        else:
            return Response({
                "success": False,
                "error": message,
                "action": action_performed,
                "type": webhook_type,
                "subscriptions": updated_status
            }, status=status.HTTP_400_BAD_REQUEST)
