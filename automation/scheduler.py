import logging
import os
import time
import threading
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections, models
from django.utils import timezone

logger = logging.getLogger(__name__)

# ==============================================================================
# ⚙️ AUTOMATION SCHEDULER & DISPATCHER (Master Process Runner)
# ==============================================================================
MAX_WORKERS = 2          # 2 parallel tasks at a time
MAX_MEMORY_MB =0         # 250 MB memory limit
CART_WAIT_MINUTES = 30      # 30 minutes wait for Abandoned Cart/Checkout
BROWSE_WAIT_MINUTES = 60    # 60 minutes wait for Browse Abandonment
BATCH_SIZE = 30             # Max batch per check
CHECK_INTERVAL_SECONDS = 60 # Check DB every 60 seconds
# ==============================================================================


def _get_current_memory_mb():
    """Returns physical resident memory (RSS) consumed in Megabytes."""
    try:
        import resource, sys
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / (1024 * 1024) if sys.platform == 'darwin' else rss / 1024
    except Exception:
        return 0.0

def _ensure_django_ready():
    """
    Guarantees Django apps and settings are loaded before accessing models.
    Critical when running in Gunicorn master process or daemon threads.
    """
    try:
        from django.apps import apps
        if not apps.ready:
            import os
            import django
            if not os.environ.get('DJANGO_SETTINGS_MODULE'):
                os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'email_marketing_ms_django_backend.settings')
            django.setup()
    except Exception as exc:
        logger.error(f"[AUTOMATION SCHEDULER] Django initialization error: {exc}")

def process_due_automations():
    """
    Checks for due waiting notifications and processes up to MAX_WORKERS in parallel.
    Sets status to InProgress when picked up, then dispatches.
    """
    try:
        _ensure_django_ready()
        from automation.models import NotificationSend
        from automation.services import dispatch_automation_email

        close_old_connections()
        now = timezone.now()
        cart_cutoff = now - timedelta(minutes=CART_WAIT_MINUTES)
        browse_cutoff = now - timedelta(minutes=BROWSE_WAIT_MINUTES)

        due_records = list(
            NotificationSend.objects.filter(status="Waiting")
            .filter(
                models.Q(
                    automation__external_name__in=["checkouts/create", "carts/create"],
                    created_at__lte=cart_cutoff,
                )
                | models.Q(
                    automation__external_name__in=["browse_abandonment", "browse/abandonment"],
                    created_at__lte=browse_cutoff,
                )
            )
            .order_by("created_at")[:BATCH_SIZE]
        )

        if not due_records:
            return

        print(f"[AUTOMATION SCHEDULER] 📬 Found {len(due_records)} due record(s). RAM: {_get_current_memory_mb():.1f}MB. Processing up to {MAX_WORKERS} in parallel...")

        # 1. Mark status as InProgress immediately to prevent duplicate parallel pickup
        ids_to_process = []
        for rec in due_records:
            rec.status = "InProgress"
            rec.save(update_fields=["status", "updated_at"])
            ids_to_process.append(rec.id)

        # 2. Dispatch using ThreadPoolExecutor (max_workers=MAX_WORKERS)
        def _dispatch_single(record_id):
            close_old_connections()
            try:
                dispatch_automation_email(record_id, async_mode=False)
            except Exception as e:
                logger.error(f"[AUTOMATION SCHEDULER] Error dispatching #{record_id}: {e}", exc_info=True)
            finally:
                close_old_connections()

        if MAX_WORKERS <= 1:
            for rid in ids_to_process:
                _dispatch_single(rid)
        else:
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                executor.map(_dispatch_single, ids_to_process)

    except Exception as exc:
        logger.error(f"[AUTOMATION SCHEDULER ERROR] {exc}", exc_info=True)
    finally:
        try:
            close_old_connections()
            import gc
            gc.collect()
        except Exception:
            pass


_scheduler_started = False


def start_automation_scheduler():
    """
    Starts the background scheduler loop in a single daemon thread.
    Safe to call multiple times (guarded by _scheduler_started flag).
    Enforces the MAX_MEMORY_MB limit.
    """
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    _ensure_django_ready()

    # Enforce 250 MB memory limit safely
    if MAX_MEMORY_MB > 0:
        try:
            import resource
            max_bytes = MAX_MEMORY_MB * 1024 * 1024
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            if hard != resource.RLIM_INFINITY and max_bytes > hard:
                max_bytes = hard
            resource.setrlimit(resource.RLIMIT_AS, (max_bytes, hard))
            print(f"[AUTOMATION SCHEDULER] 🛡️ Memory limit enforced: {MAX_MEMORY_MB} MB")
        except Exception as e:
            logger.debug(f"Unable to set OS memory limit: {e}")

    def _loop():
        print(f"AUTOMATION SCHEDULER  Started background scheduler (Workers: {MAX_WORKERS}, Interval: {CHECK_INTERVAL_SECONDS}s, Current RAM: {_get_current_memory_mb():.1f}MB)")
        while True:
            try:
                process_due_automations()
            except Exception as e:
                logger.error(f"[AUTOMATION SCHEDULER LOOP ERROR] {e}")
            time.sleep(CHECK_INTERVAL_SECONDS)

    t = threading.Thread(target=_loop, daemon=True, name="automation_master_scheduler")
    t.start()
