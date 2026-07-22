import requests
from django.conf import settings as settings_conf
from django.shortcuts import redirect
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny

from EmailMarketing.Views.base import StoreAuthenticatedMixin
from Accounts.models import Store, Contact
from shopify_integration.models import ShopifySettings
from shopify_integration.serializers import ShopifySettingsSerializer
from shopify_integration.sync import sync_customers_in_background

def clean_domain(domain):
    if not domain:
        return ""
    return domain.replace("https://", "").replace("http://", "").split("/")[0].strip().lower()

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
            
        if shopify_access_token:
            settings_obj.shopify_access_token = shopify_access_token
            
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
            # Fallback: check ShopifySettings
            settings_obj = ShopifySettings.objects.filter(shop_url=clean_host).first()
            if settings_obj:
                store = settings_obj.store
            else:
                # If still not found, fallback to first store to prevent crash
                store = Store.objects.first()
                if not store:
                    return HttpResponse("No stores configured in the database yet. Please register store in dashboard first.", status=400)
        
        # Retrieve credentials from settings
        api_key = getattr(settings_conf, "SHOPIFY_API_KEY", "")
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
            
        # Retrieve credentials
        api_key = getattr(settings_conf, "SHOPIFY_API_KEY", "")
        api_secret = getattr(settings_conf, "SHOPIFY_API_SECRET", "")
        
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
            print(f"[SHOPIFY CALLBACK] Exchange response JSON: {response_json}")
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
            
            # Redirect back to the frontend app
            frontend_url = getattr(settings_conf, "SHOPIFY_SITE_URL", "localhost:3000")
            if not frontend_url.startswith("http://") and not frontend_url.startswith("https://"):
                if "localhost" in frontend_url or "127.0.0.1" in frontend_url:
                    frontend_url = f"http://{frontend_url}"
                else:
                    frontend_url = f"https://{frontend_url}"
                    
            return redirect(f"{frontend_url}/settings")
        except Exception as e:
            return HttpResponse(f"An error occurred: {str(e)}", status=500)


class ShopifySubscribeAllView(StoreAuthenticatedMixin, APIView):
    """
    API view to subscribe all contacts of the active store.
    """
    def post(self, request):
        updated_count = Contact.objects.filter(store=request.store).update(accept_email_marketing=True)
        return Response({
            "message": f"Successfully subscribed {updated_count} contacts.",
            "updated_count": updated_count
        })


class ShopifySyncTriggerView(StoreAuthenticatedMixin, APIView):
    """
    API view to trigger synchronization of contacts from Shopify in the background.
    """
    def post(self, request):
        settings_obj = ShopifySettings.objects.filter(store=request.store).first()
        if not settings_obj or not settings_obj.shopify_access_token:
            return Response({
                "error": "No Shopify integration configured for this store yet."
            }, status=status.HTTP_400_BAD_REQUEST)
            
        sync_customers_in_background(settings_obj.shop_url, settings_obj.shopify_access_token, request.store)
        return Response({
            "message": "Shopify customer database sync started in the background."
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
        from shopify_integration.sync import get_valid_shopify_token
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
