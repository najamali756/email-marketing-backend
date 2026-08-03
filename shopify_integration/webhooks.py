import hmac
import hashlib
import base64
import json
import logging
import requests
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
from shopify_integration.sync import get_valid_shopify_token, fetch_segment_member_emails

logger = logging.getLogger(__name__)

# Standard Shopify GraphQL webhook topics to register
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

    # Resolve callback URL
    backend_base = getattr(settings_conf, "BACKEND_BASE_URL", "").rstrip("/")
    if not backend_base:
        backend_base = "https://marketing-be.technogroves.com"
    
    callback_url = custom_callback_url or f"{backend_base}/shopify/webhooks/receiver/"

    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    # Step 1: List existing webhook subscriptions via GraphQL
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
        resp = requests.post(gql_url, json={"query": list_query}, headers=headers)
        if resp.status_code == 200:
            existing_data = resp.json().get("data", {}).get("webhookSubscriptions", {}).get("edges", [])
            for edge in existing_data:
                sub_node = edge.get("node", {})
                sub_id = sub_node.get("id")
                topic = sub_node.get("topic")
                
                # Delete existing webhook if topic matches our target topics
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
                    requests.post(gql_url, json={"query": delete_mutation, "variables": {"id": sub_id}}, headers=headers)
                    logger.info(f"[SHOPIFY WEBHOOK REGISTER] Deleted old webhook {sub_id} for topic {topic}")

    except Exception as list_err:
        logger.warning(f"[SHOPIFY WEBHOOK REGISTER] Listing existing webhooks warning: {list_err}")

    # Step 2: Register fresh webhooks for each topic via GraphQL mutation
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
            res = requests.post(gql_url, json={"query": create_mutation, "variables": variables}, headers=headers)
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

        # Lookup Store settings
        settings_obj = ShopifySettings.objects.filter(shop_url=shop_domain).first()
        if not settings_obj:
            logger.warning(f"[SHOPIFY WEBHOOK RECEIVER] No store found for domain {shop_domain}")
            return HttpResponse("Shop domain not registered", status=404)

        store = settings_obj.store
        api_secret = settings_obj.get_api_secret() if settings_obj else getattr(settings_conf, "SHOPIFY_API_SECRET", "")

        # Verify HMAC Signature
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

        # -------------------------------------------------------------
        # EVENT HANDLER 1: Customer Created / Updated
        # -------------------------------------------------------------
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

        # -------------------------------------------------------------
        # EVENT HANDLER 2: Segment Created / Updated
        # -------------------------------------------------------------
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

        # -------------------------------------------------------------
        # EVENT HANDLER 3: Segment Deleted
        # -------------------------------------------------------------
        elif "SEGMENT_DELETE" in topic or "SEGMENTS_DELETE" in topic:
            shopify_id = payload.get("id")
            name = payload.get("name")
            if shopify_id or name:
                deleted_count, _ = EmailSegment.objects.filter(store=store, name=name).delete()
                logger.info(f"[SHOPIFY WEBHOOK] Deleted segment '{name}' ({deleted_count} records removed)")

        return HttpResponse("Webhook processed successfully", status=200)
