"""Create the Pinecone index matching the configured EMBEDDING_PROVIDER.

Switching embedding vendors changes the vector width, and an index's dimension
is fixed at creation — which is why the switch fails with

    Vector dimension 1024 does not match the dimension of the index 1536

rather than quietly producing bad search results. This script creates the index
the new provider needs.

It copies cloud and region from the existing index where it can, so the new one
lands in the same place as the old rather than wherever a default points. Safe
to re-run: an index that already exists at the right dimension is left alone,
and one at the WRONG dimension is reported rather than touched — deleting an
index is not something a helper script should decide to do.

    uv run python -m scripts.create_embedding_index                       # env default
    uv run python -m scripts.create_embedding_index --name my-idx         # override
    uv run python -m scripts.create_embedding_index --provider voyage     # a project's
    uv run python -m scripts.create_embedding_index --dry-run             # just report

Use --provider together with --name when creating the index for a PROJECT whose
embedding vendor differs from the deployment default: the pair is what the
project settings page stores, and the index has to be built for that pair.
"""

import argparse
import sys

import pinecone

from app.core.config import settings
from app.services.vector_service import (
    _EMBEDDING_DIMENSIONS,
    embedding_provider,
    model_for_provider,
    normalise_provider,
)

#: Where to put the index when there is no existing one to copy from.
_FALLBACK_CLOUD = "aws"
_FALLBACK_REGION = "us-east-1"

#: Cosine, deliberately. `settings.rag_min_score` is documented as a cosine
#: score in 0..1, and the relevance floor is applied to whatever Pinecone
#: returns — switch metric and that number silently means something else.
#: Voyage vectors are unit-normalised, so cosine and dot product rank
#: identically anyway; cosine is the one that keeps the threshold meaningful.
_METRIC = "cosine"


def _existing_spec(pc: pinecone.Pinecone) -> tuple[str, str] | None:
    """Cloud and region of any serverless index on this account, or None."""
    try:
        for index in pc.list_indexes():
            spec = getattr(index, "spec", None)
            serverless = getattr(spec, "serverless", None) if spec else None
            if serverless is None and isinstance(spec, dict):
                serverless = spec.get("serverless")
            if serverless is None:
                continue
            cloud = getattr(serverless, "cloud", None)
            region = getattr(serverless, "region", None)
            if cloud is None and isinstance(serverless, dict):
                cloud, region = serverless.get("cloud"), serverless.get("region")
            if cloud and region:
                return str(cloud), str(region)
    except Exception as exc:  # placement is advisory; never fatal
        print(f"  (could not read existing index placement: {exc})")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--name",
        default=None,
        help="Index name to create. Default: PINECONE_INDEX_NAME from settings.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        choices=sorted(_EMBEDDING_DIMENSIONS),
        help=(
            "Embedding vendor to size the index for. Default: the deployment's "
            "EMBEDDING_PROVIDER. Pass a project's vendor when creating its index."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be created without creating it.",
    )
    args = parser.parse_args()

    if not settings.pinecone_api_key:
        print("PINECONE_API_KEY is not set — nothing to do.")
        return 1

    provider = (
        normalise_provider(args.provider) if args.provider else embedding_provider()
    )
    dimension = _EMBEDDING_DIMENSIONS[provider]
    name = args.name or settings.pinecone_index_name

    source = "--provider" if args.provider else "EMBEDDING_PROVIDER"
    print(f"provider ({source}) : {provider}")
    print(f"embedding model    : {model_for_provider(provider)}")
    print(f"index name         : {name}")
    print(f"dimension          : {dimension}")
    print(f"metric             : {_METRIC}")

    pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)

    if pc.has_index(name):
        existing = pc.describe_index(name)
        actual = int(getattr(existing, "dimension", 0) or 0)
        if actual == dimension:
            print(
                f"\nIndex '{name}' already exists at dimension {actual}. "
                f"Nothing to do."
            )
            return 0
        # The failure this script exists to resolve. Deleting the old index
        # would throw away whatever is still in it, so say what to do instead.
        print(
            f"\nIndex '{name}' exists but is {actual}-dimensional, and "
            f"{provider} produces {dimension}. An index's dimension cannot be "
            f"changed.\n"
            f"Point PINECONE_INDEX_NAME at a NEW name and re-run, e.g.:\n"
            f"    PINECONE_INDEX_NAME={name}-{provider}\n"
            f"The existing index is left untouched."
        )
        return 1

    placement = _existing_spec(pc)
    if placement:
        cloud, region = placement
        print(f"placement          : {cloud}/{region} (copied from existing index)")
    else:
        cloud, region = _FALLBACK_CLOUD, _FALLBACK_REGION
        print(f"placement          : {cloud}/{region} (default — no index to copy)")

    if args.dry_run:
        print("\n--dry-run: not creating anything.")
        return 0

    print(f"\nCreating '{name}' ...")
    pc.create_index(
        name=name,
        dimension=dimension,
        metric=_METRIC,
        spec=pinecone.ServerlessSpec(cloud=cloud, region=region),
    )
    print(
        f"Created. Set PINECONE_INDEX_NAME={name} and re-ingest — the new index "
        f"is empty, and nothing from the old one carries over."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
