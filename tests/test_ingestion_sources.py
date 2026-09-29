"""Sources read files; they never write."""

from __future__ import annotations

from pathlib import Path

from azure.core.exceptions import ResourceNotFoundError

from pension_chat.ingestion.sources import BlobSource, LocalDirSource, SourceFile


def test_local_source_reads_a_file_and_reports_missing(tmp_path: Path) -> None:
    (tmp_path / "article_8.txt").write_bytes(b"hello")
    src = LocalDirSource(tmp_path)

    got = src.get("article_8.txt")
    assert isinstance(got, SourceFile)
    assert (got.name, got.data) == ("article_8.txt", b"hello")
    assert got.version  # mtime, non-empty
    assert src.get("absent.txt") is None


class _Props:
    etag = '"0x8DC"'


class _Downloader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.properties = _Props()

    def readall(self) -> bytes:
        return self._data


class _BlobClient:
    def __init__(self, blobs: dict[str, bytes], name: str) -> None:
        self._blobs, self._name = blobs, name

    def download_blob(self) -> _Downloader:
        if self._name not in self._blobs:
            raise ResourceNotFoundError("no such blob")
        return _Downloader(self._blobs[self._name])


class _Container:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self._blobs = blobs

    def get_blob_client(self, name: str) -> _BlobClient:
        return _BlobClient(self._blobs, name)


def test_blob_source_downloads_by_name_with_its_etag() -> None:
    src = BlobSource(_Container({"Illinois Pension Law MEABF.docx": b"docx-bytes"}))

    got = src.get("Illinois Pension Law MEABF.docx")
    assert got == SourceFile("Illinois Pension Law MEABF.docx", b"docx-bytes", '"0x8DC"')
    assert src.get("missing.docx") is None
