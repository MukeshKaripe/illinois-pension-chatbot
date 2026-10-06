"""Where statute documents come from. Sources read; they never write."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from azure.core.exceptions import ResourceNotFoundError


@dataclass(frozen=True)
class SourceFile:
    name: str       # file name within the source, e.g. "article_8.txt"
    data: bytes
    version: str    # Blob ETag, or file mtime for local files — for provenance logs


class Source(Protocol):
    def get(self, name: str) -> SourceFile | None:
        """The named file, or None when the source does not have it."""
        ...


class LocalDirSource:
    """Files in one local directory (development, tests, and the ILGA texts)."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def get(self, name: str) -> SourceFile | None:
        path = self._path / name
        if not path.is_file():
            return None
        return SourceFile(name, path.read_bytes(), str(path.stat().st_mtime_ns))


class BlobSource:
    """Files in one Azure Blob container. Replace the connection string with a
    managed identity once the cluster exists; only `from_settings` changes."""

    def __init__(self, container_client: Any) -> None:
        self._container = container_client

    @classmethod
    def from_settings(cls) -> BlobSource:
        from azure.storage.blob import ContainerClient

        from pension_chat.config import get_settings

        settings = get_settings()
        if not settings.azure_storage_connection_string or not settings.azure_storage_container:
            raise RuntimeError(
                "--source blob needs AZURE_STORAGE_CONNECTION_STRING and AZURE_STORAGE_CONTAINER in .env"
            )
        return cls(
            ContainerClient.from_connection_string(
                settings.azure_storage_connection_string.get_secret_value(), settings.azure_storage_container
            )
        )

    def get(self, name: str) -> SourceFile | None:
        try:
            downloader = self._container.get_blob_client(name).download_blob()
        except ResourceNotFoundError:
            return None
        return SourceFile(name, downloader.readall(), str(downloader.properties.etag))
