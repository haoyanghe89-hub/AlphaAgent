from types import SimpleNamespace

import pytest

from marketpulse.infrastructure.storage.models import BlobRef
from marketpulse.quant.knowledge.paired_evaluation_api import read_view


def test_verified_bytes_are_reused_only_within_one_read_and_new_request_rechecks():
    ref = BlobRef(sha256="a" * 64)
    reads = []

    class Storage:
        fail = False

        def get_bytes(self, ref):
            reads.append(ref.uri)
            if self.fail:
                raise ValueError("SYNTHETIC hash mismatch")
            return b"SYNTHETIC verified content"

    blobs = Storage()
    base = SimpleNamespace(blobs=blobs)
    first = read_view(base)
    assert first.blobs.get_bytes(ref) == first.blobs.get_bytes(ref)
    assert len(reads) == 1 and base.blobs is blobs
    blobs.fail = True
    with pytest.raises(ValueError):
        read_view(base).blobs.get_bytes(ref)
