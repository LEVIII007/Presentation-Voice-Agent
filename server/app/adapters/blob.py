"""Local-filesystem blob store. In prod this seam becomes S3/GCS."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class LocalBlobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def dir_for(self, *parts: str) -> Path:
        p = self.root.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p

    async def save(self, data: bytes, *parts: str) -> Path:
        p = self.path_for(*parts)
        await asyncio.to_thread(p.write_bytes, data)
        return p

    def delete_prefix(self, *parts: str) -> None:
        shutil.rmtree(self.root.joinpath(*parts), ignore_errors=True)
