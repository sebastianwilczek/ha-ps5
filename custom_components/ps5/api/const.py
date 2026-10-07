"""Constants for Sony's PlayStation web API (verified by hand; do not change)."""

from __future__ import annotations

AUTHZ_BASE = "https://ca.account.sony.com/api/authz/v3/oauth"
GRAPHQL_URL = "https://web.np.playstation.com/api/graphql/v1/op"
CLIENT_ID = "09515159-7237-4370-9b40-3806e67c0891"
BASIC_AUTH = "Basic MDk1MTUxNTktNzIzNy00MzcwLTliNDAtMzgwNmU2N2MwODkxOnVjUGprYTV0bnRCMktxc1A="
REDIRECT_URI = "com.scee.psxandroid.scecompcall://redirect"
SCOPE = "psn:mobile.v2.core psn:clientapp"
CLIENT_DUID_PREFIX = "0000000700090100"
APOLLO_CLIENT_NAME = "my-playstation"
APOLLO_CLIENT_VERSION = "0.62.0-20260821140605-1-g30007c97"
NPSSO_URL = "https://ca.account.sony.com/api/v1/ssocookie"  # shown to the user only, never requested by the code

HASHES = {
    "getUserDevices":        "e19583f91ba05e86572d7dc6e4a0121a75d2bc23c9fa63c3c62d1eb815769756",
    "getPurchasedGameList":  "827a423f6a8ddca4107ac01395af2ec0eafd8396fc7fa204aaf9b7ed2eefa168",
    "GetRemoteDownloadList": "69d2bd08d18da3efe2902d9a97a760d436469e89551486f5985f746a026e0038",
    "downloadProgress":      "309442955add106c4539030e34b10be8618ecb93283b411918a11657288bee52",
    "initiateDownload":      "cddb5a81da705dadf59a9ea0d791fe81d7c9631ad6a34b1a8b592d61412abb39",
    "remoteDownloadCancel":  "b50f425df4b073ed2032b61d12c742f1b0ca5671591a66d897a1cc9aac821670",
}  # fmt: skip

OP_GET_USER_DEVICES = "getUserDevices"
OP_PURCHASED_GAME_LIST = "getPurchasedGameList"
OP_REMOTE_DOWNLOAD_LIST = "GetRemoteDownloadList"
OP_DOWNLOAD_PROGRESS = "downloadProgress"
OP_INITIATE_DOWNLOAD = "initiateDownload"
OP_REMOTE_DOWNLOAD_CANCEL = "remoteDownloadCancel"

# Length of a valid NPSSO value.
NPSSO_LENGTH = 64

# Library paging (never use another size).
LIBRARY_PAGE_SIZE = 24
LIBRARY_MAX_PAGES = 50

# Queue status filter sent with GetRemoteDownloadList.
QUEUE_STATUS_TYPES = ["NOTSTARTED", "STARTED", "STOPPED", "WAITFORDOWNLOAD"]

# The target console platform for every download operation (also for PS4 titles).
TARGET_PLATFORM = "PS5"

# Placeholder duid a PS4 on the account is reported with.
PS4_PLACEHOLDER_DUID = "myself"

# A console duid ciphertext is reused for at most this long (seconds).
CONSOLE_DUID_MAX_AGE = 9 * 60

# Token lifetimes handling (seconds).
ACCESS_TOKEN_REFRESH_MARGIN = 5 * 60
REFRESH_TOKEN_REAUTH_MARGIN = 2 * 24 * 3600

# Default pause after HTTP 429 without a usable Retry-After header (seconds).
DEFAULT_RETRY_AFTER = 900

# Total timeout of a single request (seconds).
REQUEST_TIMEOUT = 30
