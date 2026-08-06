import logging
import threading
from datetime import timedelta
import requests
from django.conf import settings as settings_conf
from django.db.models.signals import post_save
from django.utils import timezone as django_timezone

from Accounts.models import Contact
from EmailMarketing.models import EmailSegment
from shopify_integration.models import ShopifySettings

logger = logging.getLogger(__name__)


def get_valid_shopify_token(store):
    """
    Checks if the access token is valid. If it's expired or about to expire in 5 minutes,
    refreshes it using the refresh token, saves it, and returns the active access token.
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    token = settings_obj.shopify_access_token
    if not token:
        logger.warning(f"[SHOPIFY TOKEN] No access token found in settings for store: {store}")
        return None

    # Sanity check: If token was saved as shpss_ (secret key), move it to custom_api_secret where it belongs
    if token.startswith("shpss_"):
        logger.error(f"[SHOPIFY TOKEN] Invalid token state for store '{store.name}': Access token starts with 'shpss_' (Secret key). Secret keys cannot be used as Admin access tokens.")
        if not settings_obj.custom_api_secret:
            settings_obj.custom_api_secret = token
            settings_obj.shopify_access_token = None
            settings_obj.save()
        return None

    # If the token doesn't expire (custom app / non-expiring token), return it directly
    if not settings_obj.shopify_refresh_token or not settings_obj.shopify_token_expires_at:
        logger.debug(f"[SHOPIFY TOKEN] Non-expiring token detected for store '{store.name}'.")
        return token

    now = django_timezone.now()
    if settings_obj.shopify_token_expires_at > now + timedelta(minutes=5):
        return settings_obj.shopify_access_token

    logger.info(f"[SHOPIFY TOKEN] Refreshing token for store '{store.name}'...")
    api_key = settings_obj.get_api_key()
    api_secret = settings_obj.get_api_secret()

    refresh_url = f"https://{settings_obj.shop_url}/admin/oauth/access_token"
    payload = {
        "client_id": api_key,
        "client_secret": api_secret,
        "grant_type": "refresh_token",
        "refresh_token": settings_obj.shopify_refresh_token,
    }

    try:
        response = requests.post(refresh_url, data=payload)
        if response.status_code != 200:
            logger.error(f"[SHOPIFY TOKEN] Refresh request failed ({response.status_code}): {response.text}")
            # If Shopify reports invalid_request or 401, token is a permanent offline token.
            # Clear refresh_token and expires_at so it uses the permanent access token without error.
            if "invalid_request" in response.text or response.status_code in (400, 401):
                logger.info(f"[SHOPIFY TOKEN] Clearing invalid refresh_token for '{store.name}' to use permanent offline token.")
                settings_obj.shopify_refresh_token = None
                settings_obj.shopify_token_expires_at = None
                settings_obj.save()
            return settings_obj.shopify_access_token

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
            logger.info(f"[SHOPIFY TOKEN] Successfully refreshed token for store '{store.name}'")
            return new_access_token
    except Exception as e:
        logger.error(f"[SHOPIFY TOKEN] Error refreshing token: {str(e)}")

    return settings_obj.shopify_access_token


def sync_customers(shop_url, access_token, store, full_resync=False, updated_at_min=None):
    """
    Synchronizes customers from the Shopify store to the local database.
    - full_resync=True: Deletes existing Shopify contacts for this store first.
    - updated_at_min='YYYY-MM-DD': Incremental sync for customers updated since date.
    """
    active_token = get_valid_shopify_token(store) or access_token

    # Disconnect Contact post_save signal to prevent loopback sync calls during bulk import
    try:
        from shopify_integration.signals import sync_contact_marketing_update_to_shopify
        post_save.disconnect(sync_contact_marketing_update_to_shopify, sender=Contact)
    except Exception as sig_err:
        logger.warning(f"[SHOPIFY SYNC] Signal disconnect warning: {sig_err}")

    if full_resync:
        logger.info(f"[SHOPIFY SYNC] Full re-sync mode active. Deleting existing Shopify contacts for store '{store.name}'...")
        Contact.objects.filter(store=store, external_id__isnull=False).exclude(external_id="").delete()

    logger.info(f"[SHOPIFY SYNC] Starting customer sync for '{shop_url}' (full_resync={full_resync}, updated_at_min={updated_at_min})...")

    url = f"https://{shop_url}/admin/api/2023-04/customers.json?limit=250"
    if updated_at_min:
        url += f"&updated_at_min={updated_at_min}"

    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    total_synced = 0
    while url:
        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                if response.status_code == 401:
                    logger.error(f"[SHOPIFY SYNC 401] Access token for store '{store.name}' ({shop_url}) was revoked or invalidated by Shopify. Re-authentication required in Settings.")
                else:
                    logger.error(f"[SHOPIFY SYNC] Request failed ({response.status_code}): {response.text}")
                break

            data = response.json()
            customers = data.get("customers", [])

            for cust in customers:
                email = cust.get("email")
                if not email:
                    continue

                first_name = cust.get("first_name", "") or ""
                last_name = cust.get("last_name", "") or ""
                phone = cust.get("phone", "") or ""
                external_id = str(cust.get("id", ""))

                # Extract marketing consent from both accepts_marketing and email_marketing_consent object
                accepts_marketing = cust.get("accepts_marketing", False)
                email_consent = cust.get("email_marketing_consent") or {}
                if isinstance(email_consent, dict) and email_consent.get("state"):
                    accepts_marketing = (email_consent.get("state") == "subscribed")

                orders_count = cust.get("orders_count", 0)
                total_spent = cust.get("total_spent", 0.0)

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
                        "country": country,
                        "raw_data": cust,
                    },
                )
                total_synced += 1

            link_header = response.headers.get("Link")
            url = None
            if link_header:
                parts = link_header.split(",")
                for part in parts:
                    if 'rel="next"' in part:
                        url = part.split(";")[0].strip("<> ")
        except Exception as e:
            logger.error(f"[SHOPIFY SYNC] Error during customer synchronization: {str(e)}")
            break

    logger.info(f"[SHOPIFY SYNC] Finished customer sync. Total synced: {total_synced}")

    # Reconnect Contact post_save signal after import completes
    try:
        from shopify_integration.signals import sync_contact_marketing_update_to_shopify
        post_save.connect(sync_contact_marketing_update_to_shopify, sender=Contact)
    except Exception as sig_err:
        logger.warning(f"[SHOPIFY SYNC] Signal reconnect warning: {sig_err}")


def fetch_segment_member_emails(shop_url, access_token, shopify_id=None, shopify_query="", store=None):
    """
    Queries Shopify GraphQL API using customerSegmentMembers(segmentId: $shopifyId)
    to fetch matching customer emails for a given segment ID.
    Also falls back to local DB evaluation of common segment query parameters.
    """
    emails = []

    # 1. Primary: Shopify GraphQL customerSegmentMembers
    if shopify_id:
        url = f"https://{shop_url}/admin/api/2023-04/graphql.json"
        headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

        segment_gid = shopify_id if str(shopify_id).startswith("gid://") else f"gid://shopify/Segment/{shopify_id}"

        gql_query = """
        query GetSegmentMembers($segmentId: ID!) {
          customerSegmentMembers(first: 250, segmentId: $segmentId) {
            edges {
              node {
                id
                defaultEmailAddress {
                  emailAddress
                }
              }
            }
          }
        }
        """

        try:
            resp = requests.post(url, json={"query": gql_query, "variables": {"segmentId": segment_gid}}, headers=headers)
            logger.info(f"[SHOPIFY SEGMENT MEMBERS] GID: '{segment_gid}' | Status: {resp.status_code}")
            
            if resp.status_code == 200:
                res_data = resp.json()
                edges = res_data.get("data", {}).get("customerSegmentMembers", {}).get("edges", [])
                for edge in edges:
                    node = edge.get("node", {})
                    email_obj = node.get("defaultEmailAddress")
                    email = email_obj.get("emailAddress") if isinstance(email_obj, dict) else None
                    if email and email not in emails:
                        emails.append(email)
                logger.info(f"[SHOPIFY SEGMENT MEMBERS] Extracted {len(emails)} emails via GraphQL for GID {segment_gid}")
        except Exception as err:
            logger.error(f"[SHOPIFY SEGMENT MEMBERS] GraphQL fetch failed for {segment_gid}: {err}")

    # 2. Secondary Fallback: Evaluate query locally on store Contacts if API returned empty
    if not emails and store and shopify_query:
        logger.info(f"[SHOPIFY SEGMENT FALLBACK] Evaluating query '{shopify_query}' on store contacts...")
        q_str = shopify_query.lower()
        local_qs = Contact.objects.filter(store=store)
        
        if "number_of_orders >= 1" in q_str or "orders_count >= 1" in q_str or "purchased at least once" in q_str:
            local_qs = local_qs.filter(total_orders__gte=1)
        elif "number_of_orders = 0" in q_str or "orders_count = 0" in q_str or "first-time" in q_str:
            local_qs = local_qs.filter(total_orders=0)
        elif "subscribed" in q_str:
            local_qs = local_qs.filter(accept_email_marketing=True)

        emails = list(local_qs.values_list("email", flat=True))
        logger.info(f"[SHOPIFY SEGMENT FALLBACK] Local query evaluation returned {len(emails)} emails")

    return emails


def sync_single_segment(store, segment_id):
    """
    Fetches the latest members from Shopify GraphQL for a single segment by ID
    and updates cached_contact_count and member_emails in filter_config.
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shopify_access_token:
        logger.warning(f"[SHOPIFY SINGLE SEGMENT] No Shopify token for store {store}")
        return None

    active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token
    
    segment = EmailSegment.objects.filter(store=store, id=segment_id).first()
    if not segment:
        return None

    shopify_id = segment.filter_config.get("shopify_id", "")
    shopify_query = segment.filter_config.get("shopify_query", "")

    member_emails = fetch_segment_member_emails(
        settings_obj.shop_url, 
        active_token, 
        shopify_id=shopify_id, 
        shopify_query=shopify_query, 
        store=store
    )
    segment.cached_contact_count = len(member_emails)
    segment.filter_config["member_emails"] = member_emails
    segment.save()

    matching_contacts = Contact.objects.filter(store=store, email__in=member_emails)
    segment.contacts.set(matching_contacts)
    logger.info(f"[SHOPIFY SINGLE SEGMENT] Refreshed segment '{segment.name}' with {matching_contacts.count()} linked contact DB records")

    return segment


def sync_segments(shop_url, access_token, store, full_resync=False):
    """
    Synchronizes customer segments from Shopify GraphQL API to local EmailSegment model.
    If full_resync=True, deletes existing segments for this store first.
    """
    if full_resync:
        logger.info(f"[SHOPIFY SEGMENT] Full re-sync mode active. Clearing existing segments for store '{store.name}'...")
        EmailSegment.objects.filter(store=store, is_default=False).delete()

    logger.info(f"[SHOPIFY SEGMENT] Starting segment sync for '{shop_url}'...")
    active_token = get_valid_shopify_token(store) or access_token
    url = f"https://{shop_url}/admin/api/2023-04/graphql.json"
    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

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
        if response.status_code != 200:
            logger.error(f"[SHOPIFY SEGMENT] Request failed ({response.status_code}): {response.text}")
            return

        data = response.json()
        if "errors" in data:
            logger.error(f"[SHOPIFY SEGMENT] GraphQL returned errors: {data['errors']}")
            return

        edges = data.get("data", {}).get("segments", {}).get("edges", [])

        for edge in edges:
            node = edge.get("node", {})
            shopify_id = node.get("id")
            name = node.get("name")
            shopify_query = node.get("query", "")

            if not name or not shopify_id:
                continue

            member_emails = fetch_segment_member_emails(
                shop_url, 
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
                },
            )

            matching_contacts = Contact.objects.filter(store=store, email__in=member_emails)
            segment.contacts.set(matching_contacts)
            logger.info(f"[SHOPIFY SEGMENT] Synced segment '{name}' with {matching_contacts.count()} linked DB contacts ({len(member_emails)} Shopify emails)")

    except Exception as e:
        logger.error(f"[SHOPIFY SEGMENT] Error syncing segments: {str(e)}")


def sync_customers_and_segments(shop_url, access_token, store):
    active_token = get_valid_shopify_token(store) or access_token
    sync_customers(shop_url, active_token, store)
    sync_segments(shop_url, active_token, store)


def sync_customers_in_background(shop_url, access_token, store, full_resync=False, updated_at_min=None):
    thread = threading.Thread(
        target=sync_customers,
        args=(shop_url, access_token, store),
        kwargs={"full_resync": full_resync, "updated_at_min": updated_at_min}
    )
    thread.daemon = True
    thread.start()


def update_customer_marketing_on_shopify(shop_url, access_token, store, external_id, accepts_marketing):
    """
    Directly updates customer email_marketing_consent parameter inside Shopify.
    Handles Shopify REST API requirements and payload fallbacks cleanly.
    """
    logger.info(f"[SHOPIFY UPDATE] Updating customer {external_id} accepts_marketing={accepts_marketing}...")
    active_token = get_valid_shopify_token(store) or access_token

    url = f"https://{shop_url}/admin/api/2023-04/customers/{external_id}.json"
    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    payload_modern = {
        "customer": {
            "id": int(external_id),
            "email_marketing_consent": {
                "state": "subscribed" if accepts_marketing else "unsubscribed",
                "opt_in_level": "single_opt_in" if accepts_marketing else "single_opt_in",
            },
        }
    }

    try:
        response = requests.put(url, json=payload_modern, headers=headers)

        # Fallback to legacy payload if status code is not 200
        if response.status_code != 200:
            payload_legacy = {
                "customer": {
                    "id": int(external_id),
                    "accepts_marketing": accepts_marketing,
                    "accepts_marketing_updated_at": django_timezone.now().isoformat(),
                }
            }
            response = requests.put(url, json=payload_legacy, headers=headers)

        if response.status_code == 200:
            res_json = response.json()
            updated_cust = res_json.get("customer")
            if updated_cust:
                Contact.objects.filter(store=store, external_id=str(external_id)).update(raw_data=updated_cust)
                logger.info(f"[SHOPIFY UPDATE] Refreshed raw_data for customer {external_id}")
        else:
            logger.error(f"[SHOPIFY UPDATE] Failed to update customer {external_id}: {response.text}")

    except Exception as e:
        logger.error(f"[SHOPIFY UPDATE] Exception updating customer {external_id}: {str(e)}")


def update_customer_marketing_on_shopify_in_background(shop_url, access_token, store, external_id, accepts_marketing):
    thread = threading.Thread(
        target=update_customer_marketing_on_shopify,
        args=(shop_url, access_token, store, external_id, accepts_marketing),
    )
    thread.daemon = True
    thread.start()


def update_contact_marketing_consent(store, contact, accepts_marketing):
    """
    Updates a single contact's marketing status in local DB, sends direct HTTP PUT to Shopify API,
    and updates raw_data in local DB.
    """
    contact.accept_email_marketing = accepts_marketing
    if accepts_marketing:
        contact.accept_email_marketing_at = django_timezone.now()
    contact.save(update_fields=["accept_email_marketing", "accept_email_marketing_at", "updated_at"])

    if contact.external_id:
        settings_obj = ShopifySettings.objects.filter(store=store).first()
        if settings_obj and settings_obj.shopify_access_token:
            active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token
            update_customer_marketing_on_shopify(
                settings_obj.shop_url,
                active_token,
                store,
                contact.external_id,
                accepts_marketing,
            )
    return contact


def bulk_update_marketing_consent_shopify(store, accepts_marketing=True, contact_ids=None):
    """
    Loops through contacts for the store (filtered by contact_ids if provided),
    sets accept_email_marketing in DB, and sends direct HTTP PUT requests to Shopify API.
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    active_token = None
    shop_url = None
    if settings_obj and settings_obj.shopify_access_token:
        shop_url = settings_obj.shop_url
        active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token

    qs = Contact.objects.filter(store=store)
    if contact_ids:
        qs = qs.filter(id__in=contact_ids)

    updated_count = 0
    for contact in qs:
        contact.accept_email_marketing = accepts_marketing
        if accepts_marketing:
            contact.accept_email_marketing_at = django_timezone.now()
        contact.save(update_fields=["accept_email_marketing", "accept_email_marketing_at", "updated_at"])
        updated_count += 1

        if contact.external_id and shop_url and active_token:
            try:
                update_customer_marketing_on_shopify(
                    shop_url,
                    active_token,
                    store,
                    contact.external_id,
                    accepts_marketing,
                )
            except Exception as err:
                logger.error(f"[SHOPIFY CONSENT UPDATE] Failed to update customer {contact.external_id}: {err}")

    logger.info(f"[SHOPIFY CONSENT UPDATE] Successfully updated {updated_count} contacts for store '{store.name}'")
    return updated_count


def bulk_subscribe_all_shopify(store, contact_ids=None):
    return bulk_update_marketing_consent_shopify(store, accepts_marketing=True, contact_ids=contact_ids)
