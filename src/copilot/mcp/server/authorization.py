"""OAuth bearer/JWT validation and MCP-to-existing-identity authorization mapping."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import jwt
from pydantic import SecretStr

from copilot.contracts import MCPAccessToken, MCPClientIdentity
from copilot.mcp.errors import MCPAuthorizationError, MCPTenantViolationError
from copilot.mcp.security.scope_mapper import MCPScopeMapper


class JWTAuthorizationVerifier:
    """Validate issuer, audience, expiry, client, subject, tenant, and scopes."""

    def __init__(
        self,
        *,
        signing_key: SecretStr,
        issuer: str,
        audience: str,
        algorithms: tuple[str, ...] = ("HS256",),
        leeway_seconds: int = 30,
    ) -> None:
        if len(signing_key.get_secret_value().encode("utf-8")) < 32:
            raise ValueError("MCP JWT signing key must contain at least 32 bytes")
        if not issuer or not audience:
            raise ValueError("MCP JWT issuer and audience are required")
        self._key = signing_key
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._leeway = leeway_seconds

    def verify(self, token: str) -> MCPAccessToken | None:
        try:
            claims = jwt.decode(
                token,
                self._key.get_secret_value(),
                algorithms=list(self._algorithms),
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={
                    "require": [
                        "exp",
                        "iat",
                        "iss",
                        "aud",
                        "sub",
                        "client_id",
                        "user_id",
                        "tenant_id",
                        "scope",
                    ]
                },
            )
        except jwt.PyJWTError:
            return None
        try:
            scopes = _string_tuple(claims["scope"], split_space=True)
            roles = _string_tuple(claims.get("roles", ()))
            data_scope = _string_tuple(claims.get("data_scope", ()))
            expires_at = datetime.fromtimestamp(int(claims["exp"]), UTC)
            issued_at = datetime.fromtimestamp(int(claims["iat"]), UTC)
            identity = MCPClientIdentity(
                client_id=str(claims["client_id"]),
                user_id=str(claims["user_id"]),
                tenant_id=str(claims["tenant_id"]),
                roles=roles,
                scopes=scopes,
                data_scope=data_scope,
                purpose=str(claims.get("purpose") or "supplier_quality_analysis.v1"),
                issuer=str(claims["iss"]),
                audience=self._audience,
                subject=str(claims["sub"]),
                expires_at=expires_at,
                authentication_source="mcp_oauth_jwt",
            )
        except (KeyError, TypeError, ValueError):
            return None
        return MCPAccessToken(
            identity=identity,
            issued_at=issued_at,
            token_fingerprint=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )


class MCPServerAuthorization:
    """Revalidate tenant and required external scopes for each provider operation."""

    def __init__(self, scope_mapper: MCPScopeMapper | None = None) -> None:
        self._scopes = scope_mapper or MCPScopeMapper()

    def authorize(
        self,
        identity: MCPClientIdentity,
        *,
        required_scopes: tuple[str, ...],
        allowed_tenants: tuple[str, ...],
    ) -> None:
        if identity.expires_at is not None and identity.expires_at <= datetime.now(UTC):
            raise MCPAuthorizationError("MCP client credential has expired")
        if allowed_tenants and identity.tenant_id not in allowed_tenants:
            raise MCPTenantViolationError("MCP client tenant is not allowed")
        self._scopes.require(identity.scopes, required_scopes)


def _string_tuple(value: object, *, split_space: bool = False) -> tuple[str, ...]:
    parts: tuple[str, ...]
    if isinstance(value, str):
        parts = tuple(value.split()) if split_space else (value,)
    elif isinstance(value, (list, tuple)):
        parts = tuple(str(item) for item in value)
    else:
        raise ValueError("claim must be a string or string collection")
    return tuple(dict.fromkeys(item for item in parts if item))


__all__ = ["JWTAuthorizationVerifier", "MCPServerAuthorization"]
