"""
Celery migration stub.

When Celery is added:
1. pip install celery redis
2. Configure CELERY_BROKER_URL in settings
3. Replace BulkEmailSender.send_async() spawn_thread call with:

    from EmailMarketing.tasks import send_campaign_task
    send_campaign_task.delay(campaign_id)
"""

# from celery import shared_task
#
#
# @shared_task(bind=True, max_retries=3)
# def send_campaign_task(self, campaign_id):
#     from EmailMarketing.BusinessLogic.BulkEmailSender import BulkEmailSender
#     BulkEmailSender(campaign_id).send_in_batches()
