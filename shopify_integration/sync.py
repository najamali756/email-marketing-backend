import logging
import threading
import time
from datetime import timedelta
import requests
from django.conf import settings as settings_conf
from django.db import connection
from django.db.models.signals import post_save
from django.utils import timezone as django_timezone

from Accounts.models import Contact
from EmailMarketing.BusinessLogic.AudienceResolver import AudienceResolver
from EmailMarketing.models import EmailSegment
from EmailMarketing.Views.Audiences import invalidate_segment_cache
from shopify_integration.models import ShopifySettings
from shopify_integration import signals

_shopify_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(max_retries=3, pool_connections=20, pool_maxsize=20)
_shopify_session.mount("https://", _adapter)
_shopify_session.mount("http://", _adapter)

logger = logging.getLogger(__name__)


def refresh_shopify_token_now(store):
    """
    Forces an immediate token refresh using the stored refresh_token.
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shopify_refresh_token:
        return None

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
        if response.status_code == 200:
            res_json = response.json()
            new_access_token = res_json.get("access_token")
            new_refresh_token = res_json.get("refresh_token")
            expires_in = res_json.get("expires_in")

            if new_access_token:
                settings_obj.shopify_access_token = new_access_token
                if new_refresh_token:
                    settings_obj.shopify_refresh_token = new_refresh_token
                settings_obj.shopify_token_expires_at = (
                    django_timezone.now() + timedelta(seconds=int(expires_in))
                    if expires_in is not None
                    else None
                )
                settings_obj.save()
                logger.info(f"[SHOPIFY TOKEN REFRESH] Successfully refreshed token for store '{store.name}'")
                return new_access_token
        else:
            logger.error(f"[SHOPIFY TOKEN REFRESH] Refresh request failed ({response.status_code}): {response.text}")
    except Exception as e:
        logger.error(f"[SHOPIFY TOKEN REFRESH] Error refreshing token: {str(e)}")

    return None


def shopify_api_request(method, url, store, headers=None, **kwargs):
    """Send a Shopify API request using persistent session pooling and retry on 401/403 or network drops."""
    request_headers = headers if headers is not None else {}
    req_func = getattr(_shopify_session, method.lower(), _shopify_session.post)

    response = None
    timeout = kwargs.pop("timeout", 45)
    for attempt in range(3):
        try:
            response = req_func(url, headers=request_headers, timeout=timeout, **kwargs)
            break
        except Exception as conn_err:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise conn_err

    if response is None or response.status_code not in (401, 403):
        return response

    new_access_token = refresh_shopify_token_now(store)
    if not new_access_token:
        logger.error(
            "[SHOPIFY API] Request returned HTTP %s and token refresh failed for store '%s'.",
            response.status_code,
            getattr(store, "name", store),
        )
        return response

    request_headers["X-Shopify-Access-Token"] = new_access_token
    return req_func(url, headers=request_headers, timeout=timeout, **kwargs)


def get_valid_shopify_token(store):
    """
    Checks if the access token is valid. If it's expired or about to expire in 5 minutes,
    refreshes it using the refresh token, saves it, and returns the active access token.
    """
    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shopify_access_token:
        logger.warning(f"[SHOPIFY TOKEN] No access token found in settings for store: {store}")
        return None

    token = settings_obj.shopify_access_token
    if token.startswith("shpss_"):
        logger.error(f"[SHOPIFY TOKEN] Invalid token state for store '{store.name}': Access token starts with 'shpss_'.")
        if not settings_obj.custom_api_secret:
            settings_obj.custom_api_secret = token
            settings_obj.shopify_access_token = None
            settings_obj.save()
        return None

    if not settings_obj.shopify_refresh_token or not settings_obj.shopify_token_expires_at:
        return token

    now = django_timezone.now()
    if settings_obj.shopify_token_expires_at > now + timedelta(minutes=5):
        return settings_obj.shopify_access_token

    logger.info(f"[SHOPIFY TOKEN] Token expiring soon for store '{store.name}'. Triggering auto-refresh...")
    return refresh_shopify_token_now(store) or settings_obj.shopify_access_token


def sync_customers(shop_url, access_token, store, full_resync=False, updated_at_min=None):
    """
    Synchronizes customers from the Shopify store to the local database.
    - full_resync=True: Deletes existing Shopify contacts for this store first.
    - updated_at_min='YYYY-MM-DD': Incremental sync for customers updated since date.
    """
    active_token = get_valid_shopify_token(store) or access_token

    try:
        post_save.disconnect(signals.sync_contact_marketing_update_to_shopify, sender=Contact)
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
            response = shopify_api_request("get", url, store, headers=headers)
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
    update_store_segments_cache(store)

    try:
        post_save.connect(signals.sync_contact_marketing_update_to_shopify, sender=Contact)
    except Exception as sig_err:
        logger.warning(f"[SHOPIFY SYNC] Signal reconnect warning: {sig_err}")


def fetch_segment_member_emails(shop_url, access_token, shopify_id=None, shopify_query="", store=None):
    """
    Fetches the full list of member emails for a Shopify segment directly from
    Shopify GraphQL API using customerSegmentMembers with cursor pagination,
    connection pooling, automatic retry, and adaptive rate-limit throttling.
    """
    emails = []

    if shopify_id:
        url = f"https://{shop_url}/admin/api/2023-04/graphql.json"
        headers = {
            "X-Shopify-Access-Token": access_token,
            "Content-Type": "application/json",
        }

        segment_gid = shopify_id if str(shopify_id).startswith("gid://") else f"gid://shopify/Segment/{shopify_id}"

        gql_query = """
        query GetSegmentMembers($segmentId: ID!, $cursor: String) {
          customerSegmentMembers(first: 250, segmentId: $segmentId, after: $cursor) {
            edges {
              cursor
              node {
                id
                defaultEmailAddress {
                  emailAddress
                }
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
        """

        cursor = None
        has_next = True
        page_count = 0
        MAX_PAGES = 160  # Supports up to 40,000 members per segment

        while has_next and page_count < MAX_PAGES:
            page_count += 1
            res_data = None

            for attempt in range(4):
                try:
                    resp = shopify_api_request(
                        "post",
                        url,
                        store,
                        headers=headers,
                        json={
                            "query": gql_query,
                            "variables": {"segmentId": segment_gid, "cursor": cursor},
                        },
                        timeout=35,
                    )
                    if resp.status_code == 429:
                        logger.warning(f"[SHOPIFY SEGMENT MEMBERS] Rate limited (429) on page {page_count}, sleeping 4s...")
                        time.sleep(4)
                        continue
                    if resp.status_code != 200:
                        logger.error(f"[SHOPIFY SEGMENT MEMBERS] GID {segment_gid} returned HTTP {resp.status_code}: {resp.text}")
                        break

                    res_data = resp.json()
                    break
                except Exception as err:
                    logger.warning(f"[SHOPIFY SEGMENT MEMBERS] Page {page_count} attempt {attempt+1} failed: {err}. Retrying in 2.5s...")
                    time.sleep(2.5)

            if not res_data or "errors" in res_data:
                if res_data and "errors" in res_data:
                    logger.error(f"[SHOPIFY SEGMENT MEMBERS] GraphQL errors for GID {segment_gid}: {res_data['errors']}")
                break

            seg_data = res_data.get("data", {}).get("customerSegmentMembers", {})
            edges = seg_data.get("edges", [])
            for edge in edges:
                node = edge.get("node", {})
                email_obj = node.get("defaultEmailAddress")
                email = email_obj.get("emailAddress") if isinstance(email_obj, dict) else None
                if email and email not in emails:
                    emails.append(email)

            page_info = seg_data.get("pageInfo", {})
            has_next = page_info.get("hasNextPage", False)
            cursor = page_info.get("endCursor")
            if not cursor or not edges:
                break

            # Adaptive rate throttling using Shopify GraphQL cost extensions
            cost = res_data.get("extensions", {}).get("cost", {})
            throttle = cost.get("throttleStatus", {})
            available = throttle.get("currentlyAvailable", 1000)
            restore_rate = throttle.get("restoreRate", 50)
            if available < 250:
                sleep_time = (300 - available) / restore_rate
                time.sleep(max(sleep_time, 0.5))
            else:
                time.sleep(0.08)

        logger.info(f"[SHOPIFY SEGMENT MEMBERS] Extracted {len(emails)} emails from Shopify for GID {segment_gid} across {page_count} pages")

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

    if member_emails:
        existing_emails = set(
            Contact.objects.filter(store=store, email__in=member_emails).values_list("email", flat=True)
        )
        missing_emails = [e for e in member_emails if e not in existing_emails]
        if missing_emails:
            new_contacts = [Contact(store=store, email=e, accept_email_marketing=True) for e in missing_emails]
            Contact.objects.bulk_create(new_contacts, ignore_conflicts=True)

    fc = {
        "shopify_id": shopify_id,
        "shopify_query": shopify_query,
        "member_emails": member_emails,
    }
    segment.cached_contact_count = len(member_emails)
    segment.filter_config = fc
    segment.save()

    matching_contacts = Contact.objects.filter(store=store, email__in=member_emails)
    segment.contacts.set(matching_contacts)
    logger.info(f"[SHOPIFY SINGLE SEGMENT] Refreshed segment '{segment.name}' with {len(member_emails)} Shopify member emails")
    invalidate_segment_cache(store.id)

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
        response = shopify_api_request("post", url, store, headers=headers, json={"query": query})
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

            if member_emails:
                existing_emails = set(
                    Contact.objects.filter(store=store, email__in=member_emails).values_list("email", flat=True)
                )
                missing_emails = [e for e in member_emails if e not in existing_emails]
                if missing_emails:
                    new_contacts = [Contact(store=store, email=e, accept_email_marketing=True) for e in missing_emails]
                    Contact.objects.bulk_create(new_contacts, ignore_conflicts=True)

            fc = {
                "shopify_id": shopify_id,
                "shopify_query": shopify_query,
                "member_emails": member_emails,
            }
            segment, _ = EmailSegment.objects.update_or_create(
                store=store,
                name=name,
                defaults={
                    "description": f"Shopify Segment Query: {shopify_query}",
                    "cached_contact_count": len(member_emails),
                    "filter_config": fc,
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


def update_store_segments_cache(store):
    """
    Quickly updates cached_contact_count for all segments of a store.
    Evaluates fast local queries in < 15ms.
    """
    try:
        segments = EmailSegment.objects.filter(store=store)
        for seg in segments:
            seg.cached_contact_count = AudienceResolver(store).count(seg.filter_config)
            seg.save(update_fields=["cached_contact_count"])
        logger.info(f"[SEGMENT CACHE AUTO-UPDATE] Updated {segments.count()} segments for store '{store.name}'")
    except Exception as e:
        logger.warning(f"[SEGMENT CACHE AUTO-UPDATE] Failed for store {store}: {e}")


def _sync_single_segment_worker(store, segment_id):
    try:
        sync_single_segment(store, segment_id)
    except Exception as e:
        logger.error(f"[SHOPIFY SINGLE SEGMENT BACKGROUND] Error syncing segment {segment_id}: {e}")
    finally:
        connection.close()


def sync_single_segment_in_background(store, segment_id):
    thread = threading.Thread(
        target=_sync_single_segment_worker,
        args=(store, segment_id),
        daemon=True
    )
    thread.start()


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
    Handles Shopify REST API requirements cleanly.
    """
    logger.info(f"[SHOPIFY UPDATE] Updating customer {external_id} accepts_marketing={accepts_marketing}...")
    active_token = get_valid_shopify_token(store) or access_token

    url = f"https://{shop_url}/admin/api/2023-04/customers/{external_id}.json"
    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    accepts_marketing_bool = bool(accepts_marketing) and str(accepts_marketing).lower() not in ("false", "0")
    # Note: Do not pass consent_updated_at as local clock skew can cause Shopify to reject with:
    # "Consent updated at must not be in the future". Shopify auto-sets the server timestamp.
    payload_modern = {
        "customer": {
            "id": int(external_id),
            "email_marketing_consent": {
                "state": "subscribed" if accepts_marketing_bool else "unsubscribed",
                "opt_in_level": "single_opt_in" if accepts_marketing_bool else "single_opt_in",
            },
        }
    }
    target_state = payload_modern["customer"]["email_marketing_consent"]["state"]
    logger.info(f"[SHOPIFY UPDATE] Customer {external_id} payload state: {target_state}")

    try:
        response = shopify_api_request("put", url, store, headers=headers, json=payload_modern)

        if response.status_code != 200:
            payload_legacy = {
                "customer": {
                    "id": int(external_id),
                    "accepts_marketing": accepts_marketing_bool,
                }
            }
            response = shopify_api_request("put", url, store, headers=headers, json=payload_legacy)

        if response.status_code == 200:
            res_json = response.json()
            updated_cust = res_json.get("customer")
            if updated_cust:
                Contact.objects.filter(store=store, external_id=str(external_id)).update(
                    raw_data=updated_cust,
                    accept_email_marketing=accepts_marketing_bool,
                    accept_email_marketing_at=django_timezone.now() if accepts_marketing_bool else None,
                )
                logger.info(f"[SHOPIFY UPDATE] Successfully synced customer {external_id} to Shopify (state: {target_state})")
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


def fetch_and_save_store_currency(store):
    """
    Fetches the store's default currency from Shopify Admin API (/admin/api/2023-04/shop.json)
    if store.store_currency is null or empty, saves it on store, and returns the currency code.
    If store.store_currency is already populated, returns it immediately.
    """
    if not store:
        return "USD"

    current_currency = getattr(store, "store_currency", None)
    if current_currency and str(current_currency).strip():
        return str(current_currency).strip()

    settings_obj = ShopifySettings.objects.filter(store=store).first()
    if not settings_obj or not settings_obj.shop_url:
        return "USD"

    active_token = get_valid_shopify_token(store) or settings_obj.shopify_access_token
    if not active_token:
        logger.warning(f"[SHOPIFY CURRENCY] No active token for store '{store.name}' ({settings_obj.shop_url})")
        return "USD"

    url = f"https://{settings_obj.shop_url}/admin/api/2023-04/shop.json"
    headers = {
        "X-Shopify-Access-Token": active_token,
        "Content-Type": "application/json",
    }

    try:
        response = shopify_api_request("get", url, store, headers=headers, timeout=10)
        if response.status_code == 200:
            shop_data = response.json().get("shop", {})
            currency = shop_data.get("currency")
            if currency:
                currency = str(currency).strip()
                store.store_currency = currency
                store.save(update_fields=["store_currency", "updated_at"])
                logger.info(f"[SHOPIFY CURRENCY] Fetched and saved currency '{currency}' for store '{store.name}'")
                return currency
        else:
            logger.error(f"[SHOPIFY CURRENCY] Failed to fetch shop.json for {settings_obj.shop_url} ({response.status_code}): {response.text}")
    except Exception as e:
        logger.error(f"[SHOPIFY CURRENCY] Exception fetching currency for {settings_obj.shop_url}: {str(e)}")

    return "USD"
