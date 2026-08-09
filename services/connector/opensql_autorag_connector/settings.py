from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

from opensql_autorag_connector.signature import DEFAULT_TOLERANCE_SECONDS


class OutlineSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTORAG_OUTLINE_", env_file=".env")

    base_url: str = "https://app.getoutline.com"
    api_key: str = ""
    webhook_secret: str = ""

    # Comma separated collection ids. Empty means every collection the API key
    # can read, which for an internal wiki is rarely what you want: retrieval has
    # no permission filter yet, so anything indexed is searchable by anyone.
    collections: str = ""

    page_size: int = 50
    timeout_seconds: float = 30.0

    # How far the `t` value in Outline-Signature may be from now. Set to 0 to
    # accept a signature of any age, which also accepts a replayed one.
    webhook_tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS

    @property
    def collection_ids(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.collections.split(",") if part.strip())


settings = OutlineSettings()
