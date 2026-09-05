"""Copy pre-projects knowledge vectors into their project namespace.

Knowledge used to live in one namespace per user (`{user_id}:knowledge`). It
now lives in one per project (`{user_id}:{project_id}:knowledge`). Pinecone is
outside the database, so the Alembic migration that added
`knowledge_sources.project_id` could not move the vectors with it — this does
that half, and it is safe to run more than once.

COPY, not move: the source namespace is left intact. Re-running is then
idempotent (upserts overwrite by id), and a mistake is recoverable without
having to re-ingest from Confluence or Jira. Delete the old namespace by hand
once you have confirmed the new one answers.

    python -m scripts.migrate_knowledge_vectors            # report only
    python -m scripts.migrate_knowledge_vectors --apply    # do the copy
"""

import argparse
import asyncio
import sys

import pinecone
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine

#: Pinecone caps a fetch by id; copying in batches keeps each request small
#: enough to succeed on a large knowledge base.
_BATCH = 100


def _copy_namespace(index, source_ns: str, target_ns: str, apply: bool) -> int:
    """Copy every vector from one namespace to another. Returns the count.

    Vector ids are preserved, so a re-run overwrites rather than duplicating —
    the ids are deterministic (`{source}_{source_id}_chunk_{i}`) and carry the
    dedupe guarantee the ingestion path already relies on.
    """
    copied = 0
    batch: list[str] = []

    # list() paginates internally; ids come back lazily so a large namespace
    # never has to be held in memory at once.
    for vector_id in index.list(namespace=source_ns):
        # The client yields either a page (list) or a single id depending on
        # version; normalise both rather than pinning to one shape.
        ids = vector_id if isinstance(vector_id, list) else [vector_id]
        batch.extend(ids)

        if len(batch) >= _BATCH:
            copied += _flush(index, batch, source_ns, target_ns, apply)
            batch = []

    if batch:
        copied += _flush(index, batch, source_ns, target_ns, apply)

    return copied


def _flush(index, ids: list[str], source_ns: str, target_ns: str, apply: bool) -> int:
    """Fetch one batch by id and upsert it into the target namespace."""
    if not apply:
        return len(ids)

    fetched = index.fetch(ids=ids, namespace=source_ns)
    vectors = [
        {
            "id": vector_id,
            "values": record.values,
            # Metadata carries the chunk text the RAG panel renders; dropping it
            # would leave retrievable vectors with nothing to show.
            "metadata": record.metadata or {},
        }
        for vector_id, record in (fetched.vectors or {}).items()
    ]
    if vectors:
        index.upsert(vectors=vectors, namespace=target_ns)
    return len(vectors)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the copy. Without it, only report what would move.",
    )
    args = parser.parse_args()

    if not settings.pinecone_api_key:
        print("PINECONE_API_KEY is not set — nothing to migrate.", file=sys.stderr)
        return 1

    # Exactly ONE target per user: their oldest project, which is where the
    # Alembic backfill put the pre-projects rows. Every legacy vector belongs to
    # that project and to no other.
    #
    # Not "every project with sources attached". Projects created after the
    # migration also carry sources, and copying the shared legacy namespace into
    # each of them would push one project's documents into the others — the
    # cross-project bleed the per-project namespaces exist to prevent, done by
    # the migration itself. That also makes the run order-independent: a user
    # who has since made a second project still migrates correctly.
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT ON (p.user_id)
                           p.user_id, p.id AS project_id, p.name
                    FROM projects p
                    WHERE EXISTS (
                        SELECT 1 FROM knowledge_sources ks
                        WHERE ks.user_id = p.user_id
                    )
                    ORDER BY p.user_id, p.created_at ASC, p.id ASC
                    """
                )
            )
        ).all()
    await engine.dispose()

    if not rows:
        print("No user with knowledge sources has a project — nothing to copy.")
        return 0

    pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
    index = pc.Index(settings.pinecone_index_name)

    print("apply" if args.apply else "DRY RUN — pass --apply to copy")
    total = 0
    for user_id, project_id, name in rows:
        source_ns = f"{user_id}:knowledge"
        target_ns = f"{user_id}:{project_id}:knowledge"
        moved = _copy_namespace(index, source_ns, target_ns, args.apply)
        total += moved
        print(f"  {name}: {moved} vector(s)")
        print(f"    {source_ns}")
        print(f"    -> {target_ns}")

    verb = "copied" if args.apply else "would copy"
    print(f"\n{verb} {total} vector(s).")
    if args.apply:
        print(
            "The old namespaces are untouched. Delete them once the app is "
            "answering from the new ones."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
