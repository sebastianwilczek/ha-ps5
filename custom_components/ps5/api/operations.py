"""Registry of the persisted GraphQL operations we use.

Variables are built with keys in the exact order Sony's website sends them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .const import (
    HASHES,
    LIBRARY_PAGE_SIZE,
    OP_DOWNLOAD_PROGRESS,
    OP_GET_USER_DEVICES,
    OP_INITIATE_DOWNLOAD,
    OP_PURCHASED_GAME_LIST,
    OP_REMOTE_DOWNLOAD_CANCEL,
    OP_REMOTE_DOWNLOAD_LIST,
    QUEUE_STATUS_TYPES,
    TARGET_PLATFORM,
)
from .errors import ApiError, WriteRejected
from .models import Console, LibraryPage, LibraryTitle, Progress, QueueItem


def get_user_devices_variables() -> dict[str, Any]:
    return {}


def purchased_game_list_variables(start: int) -> dict[str, Any]:
    return {
        "isActive": True,
        "platform": ["ps4", "ps5"],
        "size": LIBRARY_PAGE_SIZE,
        "start": start,
        "sortBy": "ACTIVE_DATE",
        "sortDirection": "desc",
    }


def remote_download_list_variables(duid: str) -> dict[str, Any]:
    return {
        "duid": duid,
        "statusTypes": list(QUEUE_STATUS_TYPES),
        "platform": TARGET_PLATFORM,
    }


def download_progress_variables(duid: str, entitlement_id: str) -> dict[str, Any]:
    return {"duid": duid, "entitlementId": entitlement_id}


def initiate_download_variables(duid: str, entitlement_id: str) -> dict[str, Any]:
    return {
        "scheduleRequests": {
            "downloadSchedules": [
                {
                    "duid": duid,
                    "entitlementId": entitlement_id,
                    "platform": TARGET_PLATFORM,
                }
            ]
        }
    }


def remote_download_cancel_variables(duid: str, entitlement_id: str) -> dict[str, Any]:
    return {
        "updateRequests": {
            "downloadUpdates": [
                {
                    "duid": duid,
                    "entitlementId": entitlement_id,
                    "platform": TARGET_PLATFORM,
                    "reasonCode": "1",
                    "status": "USERCANCELLED",
                }
            ]
        }
    }


def _field(data: dict[str, Any], key: str, operation: str) -> Any:
    if key not in data:
        raise ApiError(f"{operation}: missing {key}", operation)
    return data[key]


def parse_user_devices(data: dict[str, Any]) -> list[Console]:
    raw = _field(data, "deviceStorageDetailsRetrieve", OP_GET_USER_DEVICES)
    if not isinstance(raw, list):
        raise ApiError("getUserDevices: unexpected shape", OP_GET_USER_DEVICES)
    return [
        console
        for item in raw
        if isinstance(item, dict) and (console := Console.from_api(item)) is not None
    ]


def parse_purchased_game_list(data: dict[str, Any]) -> LibraryPage:
    raw = _field(data, "purchasedTitlesRetrieve", OP_PURCHASED_GAME_LIST)
    if not isinstance(raw, dict) or not isinstance(raw.get("games"), list):
        raise ApiError("getPurchasedGameList: unexpected shape", OP_PURCHASED_GAME_LIST)
    page_info = raw.get("pageInfo")
    if not isinstance(page_info, dict) or not isinstance(page_info.get("isLast"), bool):
        raise ApiError("getPurchasedGameList: missing pageInfo", OP_PURCHASED_GAME_LIST)
    total = page_info.get("totalCount")
    return LibraryPage(
        titles=tuple(
            title
            for game in raw["games"]
            if isinstance(game, dict) and (title := LibraryTitle.from_api(game)) is not None
        ),
        is_last=page_info["isLast"],
        total_count=total if isinstance(total, int) else None,
    )


def parse_remote_download_list(data: dict[str, Any]) -> list[QueueItem]:
    raw = _field(data, "remoteDownloadStatusesRetrieve", OP_REMOTE_DOWNLOAD_LIST)
    if not isinstance(raw, dict) or not isinstance(raw.get("downloadStatuses"), list):
        raise ApiError("GetRemoteDownloadList: unexpected shape", OP_REMOTE_DOWNLOAD_LIST)
    return [
        queue_item
        for item in raw["downloadStatuses"]
        if isinstance(item, dict) and (queue_item := QueueItem.from_api(item)) is not None
    ]


def parse_download_progress(data: dict[str, Any]) -> Progress:
    raw = _field(data, "downloadProgressRetrieve", OP_DOWNLOAD_PROGRESS)
    if not isinstance(raw, dict):
        raise ApiError("downloadProgress: unexpected shape", OP_DOWNLOAD_PROGRESS)
    return Progress.from_api(raw)


def _write_parser(operation: str, key: str) -> Callable[[dict[str, Any]], None]:
    def parse(data: dict[str, Any]) -> None:
        items = _field(data, key, operation)
        if not isinstance(items, list) or not items:
            raise ApiError(f"{operation}: empty result", operation)
        for item in items:
            if not isinstance(item, dict) or "errorCode" not in item:
                raise ApiError(f"{operation}: unexpected shape", operation)
            if item["errorCode"] is not None:
                raise WriteRejected(operation, item["errorCode"], item.get("reasonCode"))

    return parse


@dataclass(frozen=True, slots=True)
class Operation:
    """A persisted GraphQL operation."""

    name: str
    write: bool
    build_variables: Callable[..., dict[str, Any]]
    parse: Callable[[dict[str, Any]], Any]

    @property
    def sha256_hash(self) -> str:
        return HASHES[self.name]

    @property
    def method(self) -> str:
        return "POST" if self.write else "GET"


OPERATIONS: dict[str, Operation] = {
    op.name: op
    for op in (
        Operation(OP_GET_USER_DEVICES, False, get_user_devices_variables, parse_user_devices),
        Operation(
            OP_PURCHASED_GAME_LIST,
            False,
            purchased_game_list_variables,
            parse_purchased_game_list,
        ),
        Operation(
            OP_REMOTE_DOWNLOAD_LIST,
            False,
            remote_download_list_variables,
            parse_remote_download_list,
        ),
        Operation(
            OP_DOWNLOAD_PROGRESS, False, download_progress_variables, parse_download_progress
        ),
        Operation(
            OP_INITIATE_DOWNLOAD,
            True,
            initiate_download_variables,
            _write_parser(OP_INITIATE_DOWNLOAD, "remoteDownloadSchedule"),
        ),
        Operation(
            OP_REMOTE_DOWNLOAD_CANCEL,
            True,
            remote_download_cancel_variables,
            _write_parser(OP_REMOTE_DOWNLOAD_CANCEL, "remoteDownloadCancel"),
        ),
    )
}
