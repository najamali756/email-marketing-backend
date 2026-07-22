from django.db.models.signals import post_save
from django.dispatch import receiver
from Accounts.models import Contact
from shopify_integration.models import ShopifySettings
from shopify_integration.sync import update_customer_marketing_on_shopify_in_background

@receiver(post_save, sender=Contact)
def sync_contact_marketing_update_to_shopify(sender, instance, **kwargs):
    # Only sync if the contact is linked to a store and has a Shopify external ID
    if instance.store and instance.external_id:
        settings_obj = ShopifySettings.objects.filter(store=instance.store).first()
        if settings_obj and settings_obj.shopify_access_token:
            update_customer_marketing_on_shopify_in_background(
                shop_url=settings_obj.shop_url,
                access_token=settings_obj.shopify_access_token,
                store=instance.store,
                external_id=instance.external_id,
                accepts_marketing=instance.accept_email_marketing
            )
