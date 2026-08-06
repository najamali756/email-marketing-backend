from django.urls import re_path as url

from EmailMarketing.Views.Audiences import AudienceEstimateView, EmailSegmentListCreateView
from EmailMarketing.Views.BrandSettings import EmailBrandSettingsView
from EmailMarketing.Views.Campaigns import (
    BuildCampaignAudienceView,
    CampaignRecipientsView,
    EmailCampaignDetailView,
    EmailCampaignListCreateView,
    SendCampaignView,
    UploadCampaignRecipientsView,
    PauseCampaignView,
    ResumeCampaignView,
    CancelCampaignView,
)
from EmailMarketing.Views.Templates import EmailTemplateDetailView, EmailTemplateListCreateView
from EmailMarketing.Views.Media import EmailTemplateMediaListView, EmailTemplateMediaDetailView
from EmailMarketing.Views.Unsubscribe import EmailUnsubscribeView
from EmailMarketing.Views.SenderIdentity import StoreSenderIdentityView, ValidateSenderIdentityView

urlpatterns = [
    url(r"^brandSettings$", EmailBrandSettingsView.as_view()),
    url(r"^sender-identity$", StoreSenderIdentityView.as_view()),
    url(r"^sender-identity/validate$", ValidateSenderIdentityView.as_view()),
    url(r"^templates$", EmailTemplateListCreateView.as_view()),
    url(r"^templates/(?P<pk>\d+)$", EmailTemplateDetailView.as_view()),
    url(r"^templates/media$", EmailTemplateMediaListView.as_view()),
    url(r"^templates/media/(?P<pk>\d+)$", EmailTemplateMediaDetailView.as_view()),
    url(r"^segments$", EmailSegmentListCreateView.as_view()),
    url(r"^audiences/estimate$", AudienceEstimateView.as_view()),
    url(r"^campaigns$", EmailCampaignListCreateView.as_view()),
    url(r"^campaigns/(?P<pk>\d+)$", EmailCampaignDetailView.as_view()),
    url(r"^campaigns/(?P<campaign_id>\d+)/buildAudience$", BuildCampaignAudienceView.as_view()),
    url(r"^campaigns/(?P<campaign_id>\d+)/send$", SendCampaignView.as_view()),
    url(r"^campaigns/(?P<campaign_id>\d+)/pause$", PauseCampaignView.as_view()),
    url(r"^campaigns/(?P<campaign_id>\d+)/resume$", ResumeCampaignView.as_view()),
    url(r"^campaigns/(?P<campaign_id>\d+)/cancel$", CancelCampaignView.as_view()),
    url(r"^campaigns/(?P<campaign_id>\d+)/recipients$", CampaignRecipientsView.as_view()),
    url(r"^campaigns/(?P<campaign_id>\d+)/upload-recipients$", UploadCampaignRecipientsView.as_view()),
    url(r"^unsubscribe$", EmailUnsubscribeView.as_view()),
]
