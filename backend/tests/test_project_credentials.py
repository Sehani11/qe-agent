"""Credential resolution: project first, environment as fallback.

The rule under test is all-or-nothing per integration. Field-by-field fallback
would mix one Atlassian instance's URL with another's token — an authentication
failure at best, and a credential crossing a tenant boundary at worst.
"""

import uuid

import pytest

from app.core.crypto import encrypt, generate_key
from app.models.project import Project
from app.services.project_config_service import (
    confluence_credentials_for,
    github_credentials_for,
    jira_credentials_for,
    missing_credentials_message,
)


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.core.crypto.settings.credential_encryption_key", generate_key()
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    """A fully configured environment, so any leak into a project result shows."""
    for field, value in {
        "jira_base_url": "https://env.atlassian.net",
        "jira_user_email": "env@example.com",
        "jira_api_token": "env-jira-token",
        "confluence_base_url": "https://env.atlassian.net/wiki",
        "confluence_user_email": "env@example.com",
        "confluence_api_token": "env-confluence-token",
        "github_access_token": "env-github-token",
    }.items():
        monkeypatch.setattr(
            f"app.services.project_config_service.settings.{field}", value
        )


def _project(**over) -> Project:
    return Project(id=uuid.uuid4(), user_id="u", name="P", **over)


class TestJira:
    def test_a_configured_project_supplies_the_whole_set(self) -> None:
        creds = jira_credentials_for(
            _project(
                jira_base_url="https://acme.atlassian.net",
                jira_user_email="qa@acme.test",
                jira_api_token_encrypted=encrypt("acme-token"),
            )
        )

        assert creds.source == "project"
        assert creds.api_token == "acme-token"
        # Crucially, NOT the environment's URL or email.
        assert creds.base_url == "https://acme.atlassian.net"
        assert creds.user_email == "qa@acme.test"

    def test_a_project_without_a_token_falls_back_entirely(self) -> None:
        """Its own base URL must NOT be paired with the environment's token.

        That combination would send the deployment's credential to whatever
        Atlassian instance the project happens to name.
        """
        creds = jira_credentials_for(
            _project(
                jira_base_url="https://acme.atlassian.net",
                jira_api_token_encrypted="",
            )
        )

        assert creds.source == "environment"
        assert creds.base_url == "https://env.atlassian.net"
        assert creds.api_token == "env-jira-token"

    def test_no_project_uses_the_environment(self) -> None:
        creds = jira_credentials_for(None)
        assert creds.source == "environment"
        assert creds.api_token == "env-jira-token"

    def test_an_undecryptable_token_is_treated_as_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rotated key must not take down every request for that project.

        Falling back keeps the app usable; the settings page still shows the
        credential as configured, which is where the mismatch gets corrected.
        """
        stored = encrypt("acme-token")
        monkeypatch.setattr(
            "app.core.crypto.settings.credential_encryption_key", generate_key()
        )

        creds = jira_credentials_for(
            _project(jira_api_token_encrypted=stored)
        )
        assert creds.source == "environment"


class TestConfluence:
    def test_project_credentials_win(self) -> None:
        creds = confluence_credentials_for(
            _project(
                confluence_base_url="https://acme.atlassian.net/wiki",
                confluence_user_email="qa@acme.test",
                confluence_api_token_encrypted=encrypt("acme-conf"),
            )
        )
        assert creds.source == "project"
        assert creds.api_token == "acme-conf"
        assert creds.base_url == "https://acme.atlassian.net/wiki"

    def test_falls_back_whole_when_no_token(self) -> None:
        creds = confluence_credentials_for(
            _project(confluence_base_url="https://acme.atlassian.net/wiki")
        )
        assert creds.source == "environment"
        assert creds.base_url == "https://env.atlassian.net/wiki"


class TestGitHub:
    def test_project_token_wins(self) -> None:
        creds = github_credentials_for(
            _project(
                github_repo="acme/app",
                github_access_token_encrypted=encrypt("ghp_project"),
            )
        )
        assert creds.source == "project"
        assert creds.access_token == "ghp_project"
        assert creds.default_repo == "acme/app"

    def test_repo_is_kept_even_when_the_token_falls_back(self) -> None:
        """A repo name is not a credential, so there is nothing to cross.

        A project naming a public repo without storing a token is a normal
        setup, and losing the repo would make the form forget its default.
        """
        creds = github_credentials_for(_project(github_repo="acme/public"))

        assert creds.source == "environment"
        assert creds.access_token == "env-github-token"
        assert creds.default_repo == "acme/public"


class TestMessages:
    def test_message_names_where_to_fix_it(self) -> None:
        """"Not configured" is unactionable without saying which of the two."""
        assert "project settings" in missing_credentials_message("Jira", "project")
        assert "environment" in missing_credentials_message("Jira", "environment")
