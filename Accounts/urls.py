from django.urls import re_path as url

from Accounts.views import (
    LoginView,
    LogoutView,
    MeView,
    RegisterView,
    UserListCreateView,
    UserDetailView,
    ClientListCreateView,
    ClientDetailView,
)
from Accounts.views_external import (
    ExternalUserCreateView,
    ExternalGenerateTempTokenView,
    ExternalExchangeTempTokenView,
)

urlpatterns = [
    url(r"^register/?$", RegisterView.as_view()),
    url(r"^login/?$", LoginView.as_view()),
    url(r"^logout/?$", LogoutView.as_view()),
    url(r"^me/?$", MeView.as_view()),
    url(r"^users/?$", UserListCreateView.as_view()),
    url(r"^users/(?P<pk>\d+)/?$", UserDetailView.as_view()),
    url(r"^clients/?$", ClientListCreateView.as_view()),
    url(r"^clients/(?P<pk>\d+)/?$", ClientDetailView.as_view()),

    # External SSO & User Provisioning APIs
    url(r"^external/create-user/?$", ExternalUserCreateView.as_view(), name="external_create_user"),
    url(r"^external/generate-token/?$", ExternalGenerateTempTokenView.as_view(), name="external_generate_token"),
    url(r"^external/exchange-token/?$", ExternalExchangeTempTokenView.as_view(), name="external_exchange_token"),
]
