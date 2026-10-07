"""Constants for the PS5 integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ps5"

CONF_NPSSO: Final = "npsso"
CONF_NPSSO_EXPIRES_AT: Final = "npsso_expires_at"
CONF_CLIENT_DUID: Final = "client_duid"
CONF_REFRESH_TOKEN: Final = "refresh_token"
CONF_REFRESH_TOKEN_EXPIRES_AT: Final = "refresh_token_expires_at"
CONF_ACCOUNT_ID: Final = "account_id"
CONF_CONSOLE_NAME: Final = "console_name"

MANUFACTURER: Final = "Sony Interactive Entertainment"
MODEL: Final = "PlayStation 5"

IDLE_INTERVAL: Final = timedelta(minutes=10)
ACTIVE_INTERVAL: Final = timedelta(seconds=60)
LIBRARY_INTERVAL: Final = timedelta(hours=12)
LIBRARY_RETRY_INTERVAL: Final = timedelta(hours=1)
MAX_BACKOFF: Final = timedelta(minutes=60)
FAILURES_BEFORE_UNAVAILABLE: Final = 3
REFRESH_DELAY_AFTER_WRITE: Final = 5  # seconds
NPSSO_WARNING: Final = timedelta(days=7)

LIBRARY_STORAGE_VERSION: Final = 1

ISSUE_NPSSO_EXPIRING: Final = "npsso_expiring"
ISSUE_CONSOLE_NOT_FOUND: Final = "console_not_found"
ISSUE_API_CHANGED: Final = "api_changed"

EVENT_STARTED: Final = "started"
EVENT_COMPLETED: Final = "completed"
EVENT_CANCELLED: Final = "cancelled"

SERVICE_START_DOWNLOAD: Final = "start_download"
SERVICE_CANCEL_DOWNLOAD: Final = "cancel_download"
SERVICE_GET_LIBRARY: Final = "get_library"

ATTR_DEVICE_ID: Final = "device_id"
ATTR_ENTITLEMENT_ID: Final = "entitlement_id"
ATTR_PLATFORM: Final = "platform"
ATTR_INSTALLED: Final = "installed"

SIGNAL_SELECTION_CHANGED: Final = "ps5_selection_changed_{}"
