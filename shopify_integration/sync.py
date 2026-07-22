import threading
import requests
import logging
from datetime import timedelta
from django.utils import timezone as django_timezone
from django.conf import settings as settings_conf
from Accounts.models import Contact
from shopify_integration.models import ShopifySettings

logger = logging.getLogger(__name__)

def get_valid_shopify_token(store):
    """
    Checks if the access token is valid. If it's expired or about to expire in 5 minutes,
    refreshes it using the refresh token, saves it, and returns the active access token.
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shopify_access_token:
        print("[SHOPIFY TOKEN] No settings or token found in DB.")
        return None
        
    # If the token doesn't expire (custom app), return it directly
    if not settings_obj.shopify_refresh_token or not settings_obj.shopify_token_expires_at:
        print("[SHOPIFY TOKEN] Non-expiring token detected. Returning directly.")
        return settings_obj.shopify_access_token
        
    now = django_timezone.now()
    # Refresh if token expires in less than 5 minutes
    if settings_obj.shopify_token_expires_at > now + timedelta(minutes=5):
        print(f"[SHOPIFY TOKEN] Active token is still valid. Expires at {settings_obj.shopify_token_expires_at}.")
        return settings_obj.shopify_access_token
        
    print("[SHOPIFY TOKEN] Token is expired or expiring soon. Refreshing...")
    # Refresh token call
    api_key = getattr(settings_conf, "SHOPIFY_API_KEY", "")
    api_secret = getattr(settings_conf, "SHOPIFY_API_SECRET", "")
    
    refresh_url = f"https://{settings_obj.shop_url}/admin/oauth/access_token"
    payload = {
        "client_id": api_key,
        "client_secret": api_secret,
        "grant_type": "refresh_token",
        "refresh_token": settings_obj.shopify_refresh_token
    }
    
    try:
        response = requests.post(refresh_url, data=payload)
        if response.status_code != 200:
            print(f"[SHOPIFY TOKEN] Refresh request failed: {response.status_code} - {response.text}")
            return settings_obj.shopify_access_token # Return existing as fallback
            
        res_json = response.json()
        new_access_token = res_json.get("access_token")
        new_refresh_token = res_json.get("refresh_token")
        expires_in = res_json.get("expires_in", 3600)
        
        if new_access_token:
            settings_obj.shopify_access_token = new_access_token
            if new_refresh_token:
                settings_obj.shopify_refresh_token = new_refresh_token
            settings_obj.shopify_token_expires_at = django_timezone.now() + timedelta(seconds=expires_in)
            settings_obj.save()
            print(f"[SHOPIFY TOKEN] Successfully refreshed token! New expiry: {settings_obj.shopify_token_expires_at}")
            return new_access_token
    except Exception as e:
        print(f"[SHOPIFY TOKEN] Error refreshing token: {str(e)}")
        
    return settings_obj.shopify_access_token

def sync_customers(shop_url, access_token, store):
    """
    Synchronizes customers from the Shopify store to the local database.
    Supports Link header pagination.
    """
    print(f"[SHOPIFY SYNC] Checking token validity for '{shop_url}'...")
    active_token = get_valid_shopify_token(store) or access_token
    
    # Disconnect Contact post_save signal to prevent loopback sync calls during import
    from django.db.models.signals import post_save
    try:
        from shopify_integration.signals import sync_contact_marketing_update_to_shopify
        post_save.disconnect(sync_contact_marketing_update_to_shopify, sender=Contact)
    except Exception as sig_err:
        print(f"[SHOPIFY SYNC] Signal disconnect warning: {sig_err}")

    print(f"[SHOPIFY SYNC] Starting customer sync for '{shop_url}'...")
    url = f"https://{shop_url}/admin/api/2023-04/customers.json?limit=250"
    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json"
    }
    
    total_synced = 0
    while url:
        try:
            print(f"[SHOPIFY SYNC] Fetching URL: {url}")
            response = requests.get(url, headers=headers)
            print(f"[SHOPIFY SYNC] Response status: {response.status_code}")
            
            if response.status_code != 200:
                print(f"[SHOPIFY SYNC] Request failed: {response.status_code} - {response.text}")
                logger.error(f"Failed to fetch customers: {response.status_code} - {response.text}")
                break
                
            data = response.json()
            customers = data.get("customers", [])
            print(f"[SHOPIFY SYNC] Found {len(customers)} customers in current batch.")
            
            for cust in customers:
                email = cust.get("email")
                if not email:
                    continue
                    
                first_name = cust.get("first_name", "") or ""
                last_name = cust.get("last_name", "") or ""
                phone = cust.get("phone", "") or ""
                external_id = str(cust.get("id", ""))
                accepts_marketing = cust.get("accepts_marketing", False)
                orders_count = cust.get("orders_count", 0)
                total_spent = cust.get("total_spent", 0.0)
                
                # Address fields
                default_address = cust.get("default_address", {}) or {}
                city = default_address.get("city", "") or ""
                country = default_address.get("country", "") or ""
                
                Contact.objects.update_or_create(
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
                        "country": country
                    }
                )
                total_synced += 1
                
            # Handle Pagination Link Header
            link_header = response.headers.get("Link")
            url = None
            if link_header:
                parts = link_header.split(",")
                for part in parts:
                    if 'rel="next"' in part:
                        url = part.split(";")[0].strip("<> ")
                        print(f"[SHOPIFY SYNC] Found next page URL: {url}")
        except Exception as e:
            print(f"[SHOPIFY SYNC] Error occurred: {str(e)}")
            logger.error(f"Error during customer synchronization: {str(e)}")
            break
            
    print(f"[SHOPIFY SYNC] Finished sync. Total customers synced: {total_synced}")
    
    # Reconnect Contact post_save signal after import completes
    try:
        post_save.connect(sync_contact_marketing_update_to_shopify, sender=Contact)
    except Exception as sig_err:
        print(f"[SHOPIFY SYNC] Signal reconnect warning: {sig_err}")


def sync_segments(shop_url, access_token, store):
    """
    Synchronizes customer segments from Shopify GraphQL API to local EmailSegment model.
    """
    print(f"[SHOPIFY SEGMENT] Starting segment sync for '{shop_url}'...")
    url = f"https://{shop_url}/admin/api/2023-04/graphql.json"
    headers = {
        "X-Shopify-Access-Token": access_token,
        "Content-Type": "application/json"
    }
    
    # GraphQL Query
    query = """
    {
      segments(first: 100) {
        edges {
          node {
            id
            name
            query
          }
        }
      }
    }
    """
    
    try:
        response = requests.post(url, json={"query": query}, headers=headers)
        print(f"[SHOPIFY SEGMENT] Response status: {response.status_code}")
        
        if response.status_code != 200:
            print(f"[SHOPIFY SEGMENT] Request failed: {response.status_code} - {response.text}")
            return
            
        data = response.json()
        if "errors" in data:
            print(f"[SHOPIFY SEGMENT] GraphQL returned errors: {data['errors']}")
            return
            
        edges = data.get("data", {}).get("segments", {}).get("edges", [])
        print(f"[SHOPIFY SEGMENT] Found {len(edges)} segments on Shopify.")
        
        from EmailMarketing.models import EmailSegment
        
        for edge in edges:
            node = edge.get("node", {})
            shopify_id = node.get("id")
            name = node.get("name")
            shopify_query = node.get("query", "")
            
            if not name or not shopify_id:
                continue
                
            EmailSegment.objects.update_or_create(
                store=store,
                name=name,
                defaults={
                    "description": f"Shopify Segment Query: {shopify_query}",
                    "filter_config": {
                        "shopify_id": shopify_id,
                        "shopify_query": shopify_query
                    }
                }
            )
            print(f"[SHOPIFY SEGMENT] Synced segment: {name}")
            
    except Exception as e:
        print(f"[SHOPIFY SEGMENT] Error syncing segments: {str(e)}")


def sync_customers_and_segments(shop_url, access_token, store):
    # Retrieve and use a valid refreshed token for all sync calls
    active_token = get_valid_shopify_token(store) or access_token
    sync_customers(shop_url, active_token, store)
    sync_segments(shop_url, active_token, store)


def sync_customers_in_background(shop_url, access_token, store):
    thread = threading.Thread(target=sync_customers_and_segments, args=(shop_url, access_token, store))
    thread.daemon = True
    thread.start()


def update_customer_marketing_on_shopify(shop_url, access_token, store, external_id, accepts_marketing):
    """
    Directly updates the customer accepts_marketing parameter inside Shopify.
    """
    print(f"[SHOPIFY UPDATE] Starting update for customer {external_id}...")
    active_token = get_valid_shopify_token(store) or access_token
    
    url = f"https://{shop_url}/admin/api/2023-04/customers/{external_id}.json"
    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json"
    }
    payload = {
        "customer": {
            "id": int(external_id),
            "accepts_marketing": accepts_marketing
        }
    }
    
    try:
        response = requests.put(url, json=payload, headers=headers)
        print(f"[SHOPIFY UPDATE] Updated customer {external_id} accepts_marketing={accepts_marketing}. Shopify status code: {response.status_code}")
    except Exception as e:
        print(f"[SHOPIFY UPDATE] Exception during customer update: {str(e)}")


def update_customer_marketing_on_shopify_in_background(shop_url, access_token, store, external_id, accepts_marketing):
    thread = threading.Thread(
        target=update_customer_marketing_on_shopify, 
        args=(shop_url, access_token, store, external_id, accepts_marketing)
    )
    thread.daemon = True
    thread.start()
