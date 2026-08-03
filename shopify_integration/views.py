import requests
import hmac
import hashlib
import base64
from django.conf import settings as settings_conf
from django.utils import timezone as django_timezone
from django.shortcuts import redirect
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

import logging
from EmailMarketing.Views.base import StoreAuthenticatedMixin
from Accounts.models import Store, Contact, Client
from shopify_integration.models import ShopifySettings
from shopify_integration.serializers import ShopifySettingsSerializer
from shopify_integration.sync import (
    sync_customers_in_background, 
    bulk_subscribe_all_shopify,
    get_valid_shopify_token,
    sync_single_segment,
    sync_segments,
)

logger = logging.getLogger(__name__)

def clean_domain(domain):
    if not domain:
        return ""
    
    url_str = str(domain).strip().lower()
    url_str = url_str.replace("https://", "").replace("http://", "")
    
    # Handle admin.shopify.com/store/store-name URLs
    if "admin.shopify.com/store/" in url_str:
        store_name = url_str.split("admin.shopify.com/store/")[1].split("/")[0].split("?")[0].strip()
        return f"{store_name}.myshopify.com"
        
    host = url_str.split("/")[0].strip()
    if host and "." not in host and not host.endswith(".myshopify.com"):
        host = f"{host}.myshopify.com"
    return host

class ShopifySettingsView(StoreAuthenticatedMixin, APIView):
    """
    API view to retrieve and update Shopify Settings for a store.
    """
    def get(self, request):
        settings_obj = ShopifySettings.objects.filter(store=request.store).first()
        if not settings_obj:
            return Response({
                "shop_url": "",
                "shopify_access_token": ""
            })
        serializer = ShopifySettingsSerializer(settings_obj)
        return Response(serializer.data)

    def post(self, request):
        shop_url = request.data.get("shop_url")
        shopify_access_token = request.data.get("shopify_access_token")
        custom_api_key = request.data.get("custom_api_key")
        custom_api_secret = request.data.get("custom_api_secret")
        
        if not shop_url:
            return Response({"error": "shop_url is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        clean_host = clean_domain(shop_url)
        
        # Get or create setting mapping
        settings_obj, created = ShopifySettings.objects.get_or_create(
            store=request.store,
            defaults={"shop_url": clean_host}
        )
        
        if not created:
            settings_obj.shop_url = clean_host
            
        if shopify_access_token is not None:
            settings_obj.shopify_access_token = shopify_access_token.strip() if shopify_access_token else ""
        if custom_api_key is not None:
            settings_obj.custom_api_key = custom_api_key.strip() if custom_api_key else ""
        if custom_api_secret is not None:
            settings_obj.custom_api_secret = custom_api_secret.strip() if custom_api_secret else ""
            
        settings_obj.save()
        
        # Automatically sync store's shop_url field
        request.store.shop_url = clean_host
        request.store.save()
        
        # Trigger background customer sync if token exists
        if settings_obj.shopify_access_token:
            sync_customers_in_background(settings_obj.shop_url, settings_obj.shopify_access_token, request.store)
            
        return Response(ShopifySettingsSerializer(settings_obj).data)


class ShopifyInstallView(APIView):
    """
    Step 2: Redirects the merchant to Shopify's OAuth authorization page.
    Exempt from token authorization checks because it is initiated via redirect.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        shop = request.GET.get("shop")
        if not shop:
            return HttpResponse("Missing shop parameter", status=400)
            
        clean_host = clean_domain(shop)
        
        # Check if shop is already authenticated
        settings_obj = ShopifySettings.objects.filter(shop_url=clean_host).first()
        frontend_url = getattr(settings_conf, "SHOPIFY_SITE_URL", "https://marketing.technogroves.com")
        if settings_obj and settings_obj.shopify_access_token:
            print(f"[SHOPIFY INSTALL] Shop '{clean_host}' is already authenticated. Redirecting to App UI.")
            return redirect(frontend_url)
        
        # Try to resolve the store by store_id parameter from the frontend first
        store_id = request.GET.get("store_id")
        store = None
        if store_id:
            try:
                store = Store.objects.filter(id=int(store_id)).first()
            except ValueError:
                pass
                
        # If not provided or invalid, resolve by shop_url domain mapping
        if not store:
            store = Store.objects.filter(shop_url=clean_host).first()
            
        if not store:
            # Create a Store automatically for new Shopify merchants / reviewer test stores
            default_client, _ = Client.objects.get_or_create(
                name="Shopify Merchants",
                defaults={"is_active": True}
            )
            store, _ = Store.objects.get_or_create(
                shop_url=clean_host,
                defaults={
                    "name": clean_host,
                    "client": default_client,
                    "is_active": True
                }
            )
        
        # Retrieve credentials (custom app or default global fallback)
        store_settings = getattr(store, "shopify_settings", None) or settings_obj
        api_key = store_settings.get_api_key() if store_settings else getattr(settings_conf, "SHOPIFY_API_KEY", "")
        scopes = getattr(settings_conf, "SHOPIFY_APP_API_SCOPE", "read_customers,write_customers")
        
        # Build redirect URL
        public_url = getattr(settings_conf, "EMAIL_MARKETING_PUBLIC_URL", "")
        if not public_url:
            public_url = f"{request.scheme}://{request.get_host()}"
        redirect_uri = f"{public_url.rstrip('/')}/shopify/callback/"
        
        # Use state to securely pass the Store ID to mapping function in callback
        state = str(store.id)
        
        auth_url = (
            f"https://{clean_host}/admin/oauth/authorize"
            f"?client_id={api_key}"
            f"&scope={scopes}"
            f"&redirect_uri={redirect_uri}"
            f"&state={state}"
        )
        return redirect(auth_url)


class ShopifyCallbackView(APIView):
    """
    Step 3 & 4: Receives temporary code from Shopify and requests access token.
    Exempt from token authorization checks because it is a direct public callback.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        code = request.GET.get("code")
        shop = request.GET.get("shop")
        state = request.GET.get("state")
        
        if not code or not shop or not state:
            return HttpResponse("Missing code, shop, or state parameters", status=400)
            
        clean_host = clean_domain(shop)
        
        # Retrieve the installing Store using state parameter
        try:
            store = Store.objects.get(id=int(state))
        except (ValueError, Store.DoesNotExist):
            return HttpResponse("Invalid state parameter: store not found", status=400)
            
        # Retrieve credentials (custom app or default global fallback)
        store_settings = ShopifySettings.objects.filter(store=store).first()
        api_key = store_settings.get_api_key() if store_settings else getattr(settings_conf, "SHOPIFY_API_KEY", "")
        api_secret = store_settings.get_api_secret() if store_settings else getattr(settings_conf, "SHOPIFY_API_SECRET", "")
        
        # Step 4: Request permanent access token
        exchange_url = f"https://{clean_host}/admin/oauth/access_token"
        payload = {
            "client_id": api_key,
            "client_secret": api_secret,
            "code": code,
            "expiring": 1
        }
        
        try:
            response = requests.post(exchange_url, data=payload)
            if response.status_code != 200:
                return HttpResponse(f"Token exchange failed: {response.status_code} - {response.text}", status=400)
                
            response_json = response.json()
            logger.info(f"[SHOPIFY CALLBACK] Exchange response JSON: {response_json}")
            access_token = response_json.get("access_token")
            
            if not access_token:
                return HttpResponse("Access token not found in response", status=400)
                
            # Clean up existing records to prevent OneToOne database conflicts for this store
            ShopifySettings.objects.filter(store=store).exclude(shop_url=clean_host).delete()
            
            # Map token to the store
            shopify_settings, _ = ShopifySettings.objects.get_or_create(
                store=store,
                defaults={"shop_url": clean_host}
            )
            shopify_settings.shopify_access_token = access_token
            shopify_settings.shop_url = clean_host
            
            # Save expiring token attributes if present in response
            refresh_token = response_json.get("refresh_token")
            expires_in = response_json.get("expires_in")
            if refresh_token:
                shopify_settings.shopify_refresh_token = refresh_token
            if expires_in:
                from django.utils import timezone as django_timezone
                from datetime import timedelta
                shopify_settings.shopify_token_expires_at = django_timezone.now() + timedelta(seconds=int(expires_in))
                
            shopify_settings.save()
            
            # Update store's shop_url field
            store.shop_url = clean_host
            store.save()
            
            # Trigger sync
            sync_customers_in_background(clean_host, access_token, store)
            
            # Redirect back to the frontend Settings > Integrations tab
            frontend_url = getattr(settings_conf, "SHOPIFY_SITE_URL", "https://marketing.technogroves.com")
            return redirect(f"{frontend_url.rstrip('/')}/settings?tab=Integrations&status=success")
        except Exception as e:
            return HttpResponse(f"An error occurred: {str(e)}", status=500)


class ShopifySubscribeAllView(StoreAuthenticatedMixin, APIView):
    """
    API view to subscribe all contacts of the active store and sync each directly to Shopify API.
    """
    def post(self, request):
        contact_ids = request.data.get("contact_ids")
        updated_count = bulk_subscribe_all_shopify(request.store, contact_ids)
        return Response({
            "message": f"Successfully subscribed {updated_count} contacts and synced with Shopify API.",
            "updated_count": updated_count
        })


class ShopifySyncTriggerView(StoreAuthenticatedMixin, APIView):
    """
    API view to trigger customer synchronization from Shopify in the background.
    Supports modes:
      - 'all' (Full Re-sync: deletes existing synced contacts and re-fetches all)
      - 'date' (Incremental date-based sync using since_date parameter)
    """
    def post(self, request):
        settings_obj = ShopifySettings.objects.filter(store=request.store).first()
        if not settings_obj or not settings_obj.shopify_access_token:
            return Response({
                "error": "No Shopify integration configured for this store yet."
            }, status=status.HTTP_400_BAD_REQUEST)

        mode = request.data.get("mode", "incremental").lower()
        since_date = request.data.get("since_date")

        full_resync = (mode == "all")
        updated_at_min = since_date if (mode == "date" and since_date) else None

        sync_customers_in_background(
            settings_obj.shop_url,
            settings_obj.shopify_access_token,
            request.store,
            full_resync=full_resync,
            updated_at_min=updated_at_min
        )

        return Response({
            "message": "Shopify customer sync started in the background.",
            "mode": mode,
            "since_date": updated_at_min
        })


class ShopifySingleSegmentSyncView(StoreAuthenticatedMixin, APIView):
    """
    POST /shopify/segments/<id>/sync/
    Re-syncs matching member contacts for a single segment from Shopify GraphQL API.
    """
    def post(self, request, segment_id):
        segment = sync_single_segment(request.store, segment_id)
        if not segment:
            return Response({"error": "Segment not found or invalid Shopify credentials"}, status=status.HTTP_404_NOT_FOUND)
            
        return Response({
            "message": f"Successfully refreshed segment '{segment.name}'.",
            "cached_contact_count": segment.cached_contact_count,
            "member_emails": segment.filter_config.get("member_emails", [])
        })


class ShopifySegmentsSyncAllView(StoreAuthenticatedMixin, APIView):
    """
    POST /shopify/sync/segments/
    Re-syncs all customer segments and member emails from Shopify GraphQL API.
    """
    def post(self, request):
        settings_obj = ShopifySettings.objects.filter(store=request.store).first()
        if not settings_obj or not settings_obj.shopify_access_token:
            return Response({"error": "No Shopify integration configured for this store yet."}, status=status.HTTP_400_BAD_REQUEST)

        full_resync = (request.data.get("mode") == "all")
        sync_segments(settings_obj.shop_url, settings_obj.shopify_access_token, request.store, full_resync=full_resync)

        return Response({
            "message": "Successfully synchronized segments and members from Shopify."
        })


class ShopifySegmentCreateView(StoreAuthenticatedMixin, APIView):
    """
    POST endpoint to create a segment on Shopify and save it locally.
    """
    def post(self, request):
        name = request.data.get("name")
        query = request.data.get("query")
        
        if not name or not query:
            return Response({"error": "name and query parameters are required"}, status=status.HTTP_400_BAD_REQUEST)
            
        settings_obj = ShopifySettings.objects.filter(store=request.store).first()
        if not settings_obj or not settings_obj.shopify_access_token:
            return Response({"error": "No Shopify integration configured for this store yet."}, status=status.HTTP_400_BAD_REQUEST)
            
        # Get valid rotated token
        active_token = get_valid_shopify_token(request.store) or settings_obj.shopify_access_token
        
        # Shopify GraphQL Segment Create mutation
        url = f"https://{settings_obj.shop_url}/admin/api/2023-04/graphql.json"
        headers = {
            "X-Shopify-Access-Token": active_token,
            "Content-Type": "application/json"
        }
        
        mutation = """
        mutation segmentCreate($name: String!, $query: String!) {
          segmentCreate(name: $name, query: $query) {
            segment {
              id
              name
              query
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        
        variables = {
            "name": name,
            "query": query
        }
        
        try:
            response = requests.post(url, json={"query": mutation, "variables": variables}, headers=headers)
            if response.status_code != 200:
                return Response({"error": f"Shopify API returned status code {response.status_code}: {response.text}"}, status=status.HTTP_400_BAD_REQUEST)
                
            res_json = response.json()
            if "errors" in res_json:
                return Response({"error": f"Shopify GraphQL returned errors: {res_json['errors']}"}, status=status.HTTP_400_BAD_REQUEST)
                
            data = res_json.get("data", {}).get("segmentCreate", {})
            user_errors = data.get("userErrors", [])
            if user_errors:
                return Response({"error": f"Shopify segment validation errors: {user_errors[0]['message']}"}, status=status.HTTP_400_BAD_REQUEST)
                
            segment_node = data.get("segment", {})
            shopify_id = segment_node.get("id")
            segment_name = segment_node.get("name")
            segment_query = segment_node.get("query")
            
            # Save locally
            from EmailMarketing.models import EmailSegment
            from EmailMarketing.Serializer.AudienceSerializer import EmailSegmentSerializer
            
            local_segment = EmailSegment.objects.create(
                store=request.store,
                name=segment_name,
                description=f"Shopify Segment Query: {segment_query}",
                filter_config={
                    "shopify_id": shopify_id,
                    "shopify_query": segment_query
                }
            )
            
            return Response({
                "message": "Segment created successfully on Shopify.",
                "segment": EmailSegmentSerializer(local_segment).data
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def verify_shopify_hmac(request):
    """
    Verifies X-Shopify-Hmac-SHA256 signature against SHOPIFY_API_SECRET.
    """
    shopify_hmac = request.headers.get("X-Shopify-Hmac-SHA256") or request.META.get("HTTP_X_SHOPIFY_HMAC_SHA256")
    if not shopify_hmac:
        return False

    api_secret = getattr(settings_conf, "SHOPIFY_API_SECRET", "")
    if not api_secret:
        print("[SHOPIFY WEBHOOK] Warning: SHOPIFY_API_SECRET is not configured in Django settings!")
        return False

    # Get raw body bytes (supports both DRF Request and standard Django HttpRequest)
    raw_body = getattr(request, "_request", request).body

    digest = hmac.new(
        api_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).digest()
    calculated_hmac = base64.b64encode(digest).decode("utf-8")

    is_valid = hmac.compare_digest(calculated_hmac, shopify_hmac)
    if not is_valid:
        print(f"[SHOPIFY WEBHOOK] HMAC verification failed: expected '{calculated_hmac}', received '{shopify_hmac}'")
    return is_valid


class ShopifyWebhookView(APIView):
    """
    Compliance Webhooks Handler for Shopify Privacy Requirements.
    Handles:
    - customers/data_request
    - customers/redact
    - shop/redact
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        # 1. HMAC Signature Verification
        if not verify_shopify_hmac(request):
            print("[SHOPIFY WEBHOOK] HMAC Signature verification failed!")
            return Response({"error": "Invalid HMAC signature"}, status=status.HTTP_401_UNAUTHORIZED)

        topic = request.headers.get("X-Shopify-Topic") or request.META.get("HTTP_X_SHOPIFY_TOPIC", "compliance")
        shop_domain = request.headers.get("X-Shopify-Shop-Domain") or request.META.get("HTTP_X_SHOPIFY_SHOP_DOMAIN", "")

        print(f"[SHOPIFY WEBHOOK] Received valid compliance webhook for topic: '{topic}' from shop: '{shop_domain}'")

        if topic == "shop/redact":
            print(f"[SHOPIFY WEBHOOK] Processing shop redact for {shop_domain}")
        elif topic == "customers/redact":
            print(f"[SHOPIFY WEBHOOK] Processing customer redact for {shop_domain}")
        elif topic == "customers/data_request":
            print(f"[SHOPIFY WEBHOOK] Processing customer data request for {shop_domain}")

        return Response({"status": "received"}, status=status.HTTP_200_OK)
