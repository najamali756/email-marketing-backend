"""
Gunicorn Master Configuration File
Loaded automatically by Gunicorn. Runs master process hooks.
"""
import logging

logger = logging.getLogger("gunicorn.error")


def when_ready(server):
    """
    Hook called in the Gunicorn MASTER process once the server is ready.
    Starts the single central automation scheduler in the background.
    """
    try:
        import os
        import django
        if not os.environ.get('DJANGO_SETTINGS_MODULE'):
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'email_marketing_ms_django_backend.settings')
        django.setup()
    except Exception:
        pass

    try:
        from automation.scheduler import start_automation_scheduler
        start_automation_scheduler()
        logger.info("[GUNICORN MASTER] 🚀 Started automation scheduler in Master process.")
    except Exception as exc:
        logger.error(f"[GUNICORN MASTER] ❌ Failed to start automation scheduler: {exc}")
