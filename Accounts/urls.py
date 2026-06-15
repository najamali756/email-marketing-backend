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

urlpatterns = [
    url(r"^register$", RegisterView.as_view()),
    url(r"^login$", LoginView.as_view()),
    url(r"^logout$", LogoutView.as_view()),
    url(r"^me$", MeView.as_view()),
    url(r"^users$", UserListCreateView.as_view()),
    url(r"^users/(?P<pk>\d+)$", UserDetailView.as_view()),
    url(r"^clients$", ClientListCreateView.as_view()),
    url(r"^clients/(?P<pk>\d+)$", ClientDetailView.as_view()),
]
