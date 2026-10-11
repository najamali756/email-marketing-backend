import os
from django.apps import AppConfig


class AutomationConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'automation'

    def ready(self):
        # Auto-start scheduler if running in dev runserver
        if os.environ.get("RUN_MAIN") == "true":
            try:
                from automation.scheduler import start_automation_scheduler
                start_automation_scheduler()
            except Exception:
                pass
