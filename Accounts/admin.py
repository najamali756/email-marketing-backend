from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from Accounts.models import Client, ClientUser, User


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "is_active", "created_at")
    search_fields = ("name",)


@admin.register(ClientUser)
class ClientUserAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "client", "role", "is_active")
    list_filter = ("role", "is_active", "client")
    search_fields = ("user__email", "client__name")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
