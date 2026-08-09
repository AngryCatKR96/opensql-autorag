from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTORAG_", env_file=".env")

    database_url: str = "postgresql://autorag:autorag@127.0.0.1:5432/autorag"
    storage_dir: Path = Path("data/documents")
    embedding_provider: str = "hash"
    embedding_model: str = "intfloat/multilingual-e5-small"
    embedding_dimension: int = 384

    # Search honours the permissions of the wiki a document was synced from, by
    # asking Outline what the caller can read. The same instance the connector
    # syncs from, so these share AUTORAG_OUTLINE_* with it.
    outline_base_url: str = "https://app.getoutline.com"
    outline_timeout_seconds: float = 10.0
    outline_page_size: int = 50

    # Token used by the MCP server, which serves one user over stdio and so has
    # no per-request place to carry a caller's token.
    outline_user_token: str = ""

    # How long a resolved set of readable collections is reused. Longer means
    # fewer calls to Outline; it also bounds how long a revoked membership keeps
    # returning results.
    access_cache_seconds: int = 60

    # Signing in with Outline instead of pasting a personal API token. Registered
    # in Outline under Settings -> Applications; the redirect URI there must match
    # the one below exactly.
    outline_oauth_client_id: str = ""
    outline_oauth_client_secret: str = ""
    outline_oauth_scope: str = "read"

    # Where this API is reachable by a browser. Only used to build the redirect
    # URI, which Outline sends the caller back to.
    public_base_url: str = "http://127.0.0.1:8000"

    # Encrypts the Outline tokens held in oauth_sessions, and is what makes a
    # session readable. Rotating it signs everybody out.
    session_secret: str = ""
    session_ttl_seconds: int = 60 * 60 * 24 * 7
    session_cookie_secure: bool = False

    oauth_login_ttl_seconds: int = 600
    oauth_discovery_cache_seconds: int = 600

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/auth/outline/callback"


settings = Settings()
