import threading
import time

# Thread-safe shared parking status
_lock = threading.Lock()
_total = 0
_free = 0
_occupied = 0
_last_update_ts = 0.0
_frame_bytes = None

def update(total: int, free: int, occupied: int) -> None:
    global _total, _free, _occupied, _last_update_ts
    with _lock:
        _total = int(max(0, total))
        _free = int(max(0, free))
        _occupied = int(max(0, occupied))
        _last_update_ts = time.time()

def get_status():
    with _lock:
        return {
            "total": _total,
            "free": _free,
            "occupied": _occupied,
            "last_update_ts": _last_update_ts,
        }


def set_frame_bytes(b: bytes) -> None:
    """Store the latest frame as JPEG bytes for the UI to read."""
    global _frame_bytes
    with _lock:
        _frame_bytes = b


def get_frame_bytes():
    """Return the latest frame bytes (or None)."""
    with _lock:
        return _frame_bytes
