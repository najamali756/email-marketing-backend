from django.contrib import admin

from EmailMarketing.models import (
    EmailBrandSettings,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailSegment,
    EmailTemplate,
    EmailUnsubscribe,
)

admin.site.register(EmailBrandSettings)
admin.site.register(EmailTemplate)
admin.site.register(EmailSegment)
admin.site.register(EmailCampaign)
admin.site.register(EmailCampaignRecipient)
admin.site.register(EmailUnsubscribe)
