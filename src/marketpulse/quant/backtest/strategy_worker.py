"""Fixed worker entry point. Reads local audited inputs only; bounded OS resources."""

import json
import sys
from pathlib import Path

from marketpulse.infrastructure.storage.local import LocalContentAddressedBlobStorage
from marketpulse.infrastructure.storage.models import BlobNotFoundError, BlobRef

from ..contracts import canonical, digest
from ..execution.limits import apply_limits
from .catalog import PackageCatalog
from .package import DataPackage, Review
from .reference import ResultReference, check
from .strategy import execute


def main():
    handle = apply_limits()

    def offline(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto"}:
            raise PermissionError("offline computation forbids network")

    sys.addaudithook(offline)
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError("worker input byte limit")
    args = json.loads(raw)
    if args.get("blob_root"):
        blobs = LocalContentAddressedBlobStorage(Path(args["blob_root"]))
        package = DataPackage.model_validate_json(
            blobs.get_bytes(BlobRef.from_uri(args["package_ref"]))
        )
        reviews = tuple(
            Review.model_validate(v)
            for v in json.loads(blobs.get_bytes(BlobRef.from_uri(args["reviews_ref"])))
        )
        references = tuple(
            ResultReference.model_validate(r)
            for r in json.loads(blobs.get_bytes(BlobRef.from_uri(args["references_ref"])))
        )

        def resolve(sha):
            try:
                return blobs.get_bytes(BlobRef(sha))
            except BlobNotFoundError:
                return None
    else:
        catalog = PackageCatalog(args["root"])
        package, reviews = catalog.read(args["package_id"])
        references = catalog.references(args["package_id"])

        def resolve(sha):
            return catalog.resolve(args["package_id"], sha)

    result = execute(
        package,
        reviews,
        args["conditions"],
        resolve,
        initial_cash=args["initial_cash"],
        risk_free_annual=args["risk_free_annual"],
        cost_pct_per_side=args["cost_pct_per_side"],
    )
    if result["execution_status"] == "COMPLETED":
        result["independent_check"] = check(result, references, resolve)
        result["reference_hash"] = digest([r.model_dump(mode="json") for r in references])
        result["output_hash"] = digest({k: v for k, v in result.items() if k != "output_hash"})
    output = canonical(result).encode()
    if len(output) > 128 * 1024 * 1024:
        raise ValueError("worker output byte limit")
    sys.stdout.buffer.write(output)
    _ = handle


if __name__ == "__main__":
    main()
