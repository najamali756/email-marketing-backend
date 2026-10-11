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
        from automation.scheduler import start_automation_scheduler
        start_automation_scheduler()
        logger.info("[GUNICORN MASTER]  Started automation scheduler in Master process.")
    except Exception as exc:
        logger.error(f"[GUNICORN MASTER]  Failed to start automation scheduler: {exc}")
