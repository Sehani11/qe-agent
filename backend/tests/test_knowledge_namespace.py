"""Knowledge namespaces are per project, not per user.

The isolation this buys is the point of projects: one project's verification
must not be able to draw on another project's documents.
"""

import uuid

import pytest

from app.services.knowledge_service import knowledge_namespace

USER = "user-a"


def test_two_projects_get_different_namespaces() -> None:
    """The isolation guarantee, stated directly."""
    a, b = uuid.uuid4(), uuid.uuid4()
    assert knowledge_namespace(USER, a) != knowledge_namespace(USER, b)


def test_two_users_get_different_namespaces() -> None:
    """Per-user separation still holds (NFR-S8), now underneath the project."""
    project = uuid.uuid4()
    assert knowledge_namespace("user-a", project) != knowledge_namespace(
        "user-b", project
    )


def test_namespace_is_stable_for_one_project() -> None:
    """Ingest and query must agree, or retrieval silently returns nothing."""
    project = uuid.uuid4()
    assert knowledge_namespace(USER, project) == knowledge_namespace(USER, project)


def test_uuid_and_string_forms_agree() -> None:
    """A UUID from the DB and a string from a request name the same place."""
    project = uuid.uuid4()
    assert knowledge_namespace(USER, project) == knowledge_namespace(
        USER, str(project)
    )


def test_no_project_reads_the_pre_projects_namespace() -> None:
    """The only route to knowledge ingested before projects existed.

    `scripts/migrate_knowledge_vectors.py` copies it forward; until that runs,
    this is where the data still is.
    """
    assert knowledge_namespace(USER, None) == f"{USER}:knowledge"


@pytest.mark.parametrize("project_id", [uuid.uuid4(), uuid.uuid4()])
def test_project_namespace_is_never_the_legacy_one(project_id: uuid.UUID) -> None:
    """A project must not collide with the pre-projects namespace.

    If it did, a migrated project would read the same vectors as an
    unmigrated client and the separation would be cosmetic.
    """
    assert knowledge_namespace(USER, project_id) != knowledge_namespace(USER, None)
