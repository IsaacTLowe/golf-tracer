"""Bearer-token auth: verify Supabase access tokens against the project's JWKS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import ApiError

ASYMMETRIC_ALGS = ["ES256", "RS256", "EdDSA"]

_bearer = HTTPBearer(auto_error=False, description="Supabase access token")


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None
    role: str
    claims: dict[str, Any]


class TokenVerifier:
    """Verifies Supabase JWTs. Keys come from JWKS; an HS256 secret is an optional fallback
    for projects still on the legacy shared secret."""

    def __init__(
        self,
        *,
        jwks_url: str | None,
        audience: str,
        hs256_secret: str | None = None,
        key_resolver: Callable[[str], Any] | None = None,
    ):
        self.audience = audience
        self.hs256_secret = hs256_secret
        if key_resolver is not None:
            self._resolve = key_resolver
        elif jwks_url:
            client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=600, timeout=10)
            self._resolve = lambda token: client.get_signing_key_from_jwt(token).key
        else:
            self._resolve = None  # type: ignore[assignment]

    def verify(self, token: str) -> AuthUser:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as e:
            raise ApiError(401, "invalid_token", "Malformed access token.") from e
        alg = header.get("alg")
        try:
            if alg == "HS256":
                if not self.hs256_secret:
                    raise ApiError(401, "invalid_token", "Unsupported token algorithm.")
                key: Any = self.hs256_secret
                algs = ["HS256"]
            elif alg in ASYMMETRIC_ALGS and self._resolve is not None:
                key = self._resolve(token)
                algs = [alg]
            else:
                raise ApiError(401, "invalid_token", "Unsupported token algorithm.")
            claims = jwt.decode(
                token, key, algorithms=algs, audience=self.audience, options={"require": ["exp", "sub", "aud"]}
            )
        except jwt.ExpiredSignatureError as e:
            raise ApiError(401, "token_expired", "Access token has expired.") from e
        except jwt.InvalidAudienceError as e:
            raise ApiError(401, "invalid_audience", "Access token has the wrong audience.") from e
        except jwt.PyJWKClientError as e:
            raise ApiError(401, "invalid_token", "Access token signing key is unknown.") from e
        except jwt.PyJWTError as e:
            raise ApiError(401, "invalid_token", "Access token is invalid.") from e
        if claims.get("role") not in (None, "authenticated"):
            raise ApiError(401, "invalid_token", "Access token is not a user token.")
        return AuthUser(id=str(claims["sub"]), email=claims.get("email"), role=str(claims.get("role")), claims=claims)


def current_user(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> AuthUser:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise ApiError(401, "unauthorized", "Missing bearer token.", headers={"WWW-Authenticate": "Bearer"})
    verifier: TokenVerifier = request.app.state.verifier
    return verifier.verify(creds.credentials)
