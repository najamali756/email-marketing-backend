from django.contrib import admin

from Stores.models import Contact, Store

@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "client", "shop_url", "is_active")
    list_filter = ("client", "is_active")
    search_fields = ("name", "shop_url")


admin.site.register(Contact)
