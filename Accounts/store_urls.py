from django.urls import re_path as url

from Accounts.views import (
    ContactBulkUpsertView,
    ContactCsvUploadView,
    ContactDetailView,
    ContactListCreateView,
    ContactSubscribeAllView,
    StoreDetailView,
    StoreListCreateView,
)

urlpatterns = [
    url(r"^$", StoreListCreateView.as_view()),
    url(r"^(?P<pk>\d+)$", StoreDetailView.as_view()),
    url(r"^contacts$", ContactListCreateView.as_view()),
    url(r"^contacts/uploadCsv$", ContactCsvUploadView.as_view()),
    url(r"^contacts/bulkUpsert$", ContactBulkUpsertView.as_view()),
    url(r"^contacts/subscribeAll$", ContactSubscribeAllView.as_view()),
    url(r"^contacts/(?P<pk>\d+)$", ContactDetailView.as_view()),
]