"""Exact builders for every request Sony receives.

Every string built here is golden-tested; keep parameter and key order as is.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote_plus, urlencode

from .const import AUTHZ_BASE, CLIENT_ID, GRAPHQL_URL, REDIRECT_URI, SCOPE


def encode_pairs(pairs: list[tuple[str, str]]) -> str:
    """Encode ordered pairs like JavaScript's URLSearchParams."""
    return urlencode(pairs, quote_via=quote_plus)


def dumps(obj: Any) -> str:
    """Serialize to compact JSON, keeping key order."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def persisted_query_extensions(sha256_hash: str) -> dict[str, Any]:
    """Return the persisted query extensions object."""
    return {"persistedQuery": {"version": 1, "sha256Hash": sha256_hash}}


def build_graphql_get_url(operation: str, variables: dict[str, Any], sha256_hash: str) -> str:
    """Build the full URL of a GraphQL read."""
    query = encode_pairs(
        [
            ("operationName", operation),
            ("variables", dumps(variables)),
            ("extensions", dumps(persisted_query_extensions(sha256_hash))),
        ]
    )
    return f"{GRAPHQL_URL}?{query}"


def build_graphql_post_body(operation: str, variables: dict[str, Any], sha256_hash: str) -> str:
    """Build the JSON body of a GraphQL write."""
    return dumps(
        {
            "operationName": operation,
            "variables": variables,
            "extensions": persisted_query_extensions(sha256_hash),
        }
    )


def build_authorize_url(client_duid: str) -> str:
    """Build the URL exchanging an NPSSO cookie for an authorization code."""
    query = encode_pairs(
        [
            ("access_type", "offline"),
            ("client_id", CLIENT_ID),
            ("redirect_uri", REDIRECT_URI),
            ("response_type", "code"),
            ("scope", SCOPE),
            ("duid", client_duid),
        ]
    )
    return f"{AUTHZ_BASE}/authorize?{query}"


def build_token_url() -> str:
    """Return the token endpoint URL."""
    return f"{AUTHZ_BASE}/token"


def build_token_body(code: str, client_duid: str) -> str:
    """Build the form body exchanging a code for tokens."""
    return encode_pairs(
        [
            ("code", code),
            ("redirect_uri", REDIRECT_URI),
            ("grant_type", "authorization_code"),
            ("token_format", "jwt"),
            ("duid", client_duid),
        ]
    )


def build_refresh_body(refresh_token: str, client_duid: str) -> str:
    """Build the form body refreshing an access token."""
    return encode_pairs(
        [
            ("refresh_token", refresh_token),
            ("grant_type", "refresh_token"),
            ("token_format", "jwt"),
            ("scope", SCOPE),
            ("duid", client_duid),
        ]
    )
