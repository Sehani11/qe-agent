"""Per-capture training consent, and the policy it can never override.

`training_opt_in` is stamped on each row at write time. Story 6.6 made it
operator configuration; it is now also a per-request choice — but only in the
direction that removes consent, never the one that grants it.
"""

import pytest

from app.api.v1.bdd import _resolve_training_opt_in


class TestResolveTrainingOptIn:
    def test_operator_no_cannot_be_overridden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deployment forbidden from training cannot be opted back in.

        This is the whole reason the two are ANDed rather than the request
        simply winning: a stale client, a hand-made request, or a UI bug must
        not be able to make protected content trainable.
        """
        monkeypatch.setattr(
            "app.api.v1.bdd.settings.training_data_opt_in", False
        )

        assert _resolve_training_opt_in(True) is False
        assert _resolve_training_opt_in(False) is False
        assert _resolve_training_opt_in(None) is False

    def test_user_may_opt_a_capture_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Where the policy allows it, the person capturing decides."""
        monkeypatch.setattr("app.api.v1.bdd.settings.training_data_opt_in", True)

        assert _resolve_training_opt_in(False) is False
        assert _resolve_training_opt_in(True) is True

    def test_absent_flag_takes_the_deployment_policy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Fails closed: a client that sends nothing inherits the real choice.

        Not "yes, train on this" — that is the failure mode the column's
        Python-side default was written to avoid.
        """
        monkeypatch.setattr("app.api.v1.bdd.settings.training_data_opt_in", True)
        assert _resolve_training_opt_in(None) is True

        monkeypatch.setattr(
            "app.api.v1.bdd.settings.training_data_opt_in", False
        )
        assert _resolve_training_opt_in(None) is False


class TestConsentReachesEveryWritePath:
    """All three BDD write paths accept the flag — an opt-out with a gap is not
    an opt-out. Uploaded rows in particular are build_dataset.py's default
    source."""

    def test_generate_and_save_requests_carry_it(self) -> None:
        from app.schemas.bdd import BDDGenerateRequest, BDDSaveRequest

        for model in (BDDGenerateRequest, BDDSaveRequest):
            assert "training_opt_in" in model.model_fields
            assert model.model_fields["training_opt_in"].default is None

    def test_upload_accepts_it_as_a_form_field(self) -> None:
        import inspect

        from app.api.v1.bdd import upload_bdd

        assert "training_opt_in" in inspect.signature(upload_bdd).parameters
