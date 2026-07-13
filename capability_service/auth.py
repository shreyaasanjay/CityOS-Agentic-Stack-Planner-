"""Bearer-token authentication and principal resolution for the capability service."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Iterable, Optional

from fastapi import HTTPException, status

from .config import CapabilityServiceConfig
from .schemas import ServicePrincipal


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    principal_id: str
    principal_type: str
    status: str
    allowed_endpoints: tuple[str, ...]


def authenticate_principal(
    authorization: Optional[str],
    *,
    config: CapabilityServiceConfig,
    principals: Iterable[ServicePrincipal],
) -> AuthenticatedPrincipal:
    supplied = _extract_bearer_token(authorization)
    matched_principal: Optional[ServicePrincipal] = None

    for principal in principals:
        expected_token = config.get_token_for_env(principal.auth_token_env)
        if not expected_token:
            continue
        if hmac.compare_digest(supplied, expected_token):
            matched_principal = principal
            break

    if matched_principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token.")
    if matched_principal.status != "enabled":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Principal is disabled.")

    return AuthenticatedPrincipal(
        principal_id=matched_principal.principal_id,
        principal_type=matched_principal.principal_type,
        status=matched_principal.status,
        allowed_endpoints=tuple(matched_principal.allowed_endpoints),
    )


def require_endpoint_access(principal: AuthenticatedPrincipal, endpoint: str) -> None:
    if endpoint not in principal.allowed_endpoints:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Principal is not authorized.")


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")
    return authorization[len("Bearer ") :]
