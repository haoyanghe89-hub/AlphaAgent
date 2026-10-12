"""Operator-installed packages; no client-supplied review or path is trusted."""

import hashlib
import json
import re
from pathlib import Path

from ..contracts import digest
from .package import DataPackage, Review

ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
HASH = re.compile(r"^[0-9a-f]{64}$")


class PackageCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def directory(self, key):
        if not ID.fullmatch(key):
            raise ValueError("invalid package identity")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("package path outside catalog")
        return path

    def read(self, key):
        path = self.directory(key)
        package = DataPackage.model_validate(self.document(path / "package.json"))
        reviews = tuple(Review.model_validate(r) for r in self.document(path / "reviews.json"))
        return package, reviews

    def document(self, path):
        path = path.resolve()
        if not path.is_relative_to(self.root) or path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("manifest outside catalog or too large")
        return json.loads(path.read_text(encoding="utf-8"))

    def resolve(self, key, sha):
        if not HASH.fullmatch(sha):
            raise ValueError("invalid content hash")
        path = (self.directory(key) / "objects" / sha).resolve()
        if not path.is_relative_to(self.directory(key)):
            raise ValueError("source outside package")
        if not path.is_file():
            return None
        if not 0 < path.stat().st_size <= 16 * 1024 * 1024:
            raise ValueError("source byte bound")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError("source content hash mismatch")
        return raw

    def list(self):
        results = []
        if not self.root.exists():
            return results
        for path in sorted(self.root.iterdir())[:100]:
            if not path.is_dir() or not ID.fullmatch(path.name):
                continue
            try:
                p, reviews = self.read(path.name)
                results.append(
                    dict(
                        id=path.name,
                        start=str(p.start),
                        end=str(p.end),
                        securities=len(p.securities),
                        sessions=len(p.sessions),
                        package_hash=digest(p.model_dump(mode="json")),
                        review_count=len(reviews),
                        status="PENDING_ADMISSION",
                    )
                )
            except (OSError, ValueError, KeyError):
                results.append(dict(id=path.name, status="INVALID"))
        return results

    def references(self, key):
        from .reference import ResultReference

        path = self.directory(key) / "references.json"
        raw = self.document(path) if path.is_file() else []
        if not isinstance(raw, list) or len(raw) > 2:
            raise ValueError("bounded independent references required")
        return tuple(ResultReference.model_validate(r) for r in raw)
