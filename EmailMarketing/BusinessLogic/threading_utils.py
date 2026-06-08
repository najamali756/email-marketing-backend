import threading

from django.db import close_old_connections


def spawn_thread(name, target, *args, **kwargs):
    def runner():
        close_old_connections()
        try:
            target(*args, **kwargs)
        except Exception as exc:
            print(f"Background task {name} failed: {exc}")
        finally:
            close_old_connections()

    thread = threading.Thread(target=runner, name=name, daemon=False)
    thread.start()
    return thread
