from __future__ import annotations

from hermes_managed_network.providers import (
    ManagedProvider,
    ManagedProviderPlan,
    ManagedProviderProtocol,
    ManagedProviderResult,
    ProviderOperationStatus,
    redact_sensitive_data,
)


class FakeProvider(ManagedProvider):
    provider_id = "fake"
    display_name = "Fake Provider"

    def discover(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult:
        return ManagedProviderResult(
            operation="discover",
            status=ProviderOperationStatus.OK,
            summary="fake provider discovered",
            details={"api_key": "discover-secret", "regions": ["us-east-1"]},
        )

    def plan(self, *, intent: str, config: dict[str, object] | None = None) -> ManagedProviderPlan:
        return ManagedProviderPlan(
            provider_id=self.provider_id,
            operation="apply",
            intent=intent,
            mutating=True,
            risk="high",
            dry_run=True,
            approval_required=True,
            summary="prepared fake plan",
            steps=[
                {"action": "connect", "Authorization": "Bearer top-secret"},
                {"action": "store", "refresh_token": "refresh-secret"},
            ],
            context={"password": "db-pass", "safe": "visible"},
            metadata={"api_key": "plan-secret"},
            rollback_hint="revert fake changes",
        )

    def verify(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult:
        return ManagedProviderResult(
            operation="verify",
            status=ProviderOperationStatus.OK,
            summary="verified",
            details={"access_token": "verify-secret", "safe": "visible"},
        )

    def status(self, *, config: dict[str, object] | None = None) -> ManagedProviderResult:
        return ManagedProviderResult(
            operation="status",
            status=ProviderOperationStatus.OK,
            summary="healthy",
            details={"token": "status-secret", "state": "ready"},
        )


def test_provider_plan_and_result_expose_sanitized_auditable_views():
    provider = FakeProvider()

    plan = provider.plan(intent="bootstrap", config={"api_key": "plan-secret"})
    discover = provider.discover(config={"token": "discover-token"})
    verify = provider.verify(config={"password": "verify-pass"})
    status = provider.status(config={"access_token": "status-token"})

    assert plan.context["password"] == "db-pass"
    assert plan.sanitized()["context"]["password"] == "[REDACTED]"
    assert plan.sanitized()["steps"][0]["Authorization"] == "[REDACTED]"
    assert plan.sanitized()["steps"][1]["refresh_token"] == "[REDACTED]"
    assert plan.sanitized()["context"]["safe"] == "visible"

    audit_payload = plan.audit_payload()
    assert audit_payload["provider_id"] == "fake"
    assert audit_payload["operation"] == "apply"
    assert audit_payload["risk"] == "high"
    assert audit_payload["approval_required"] is True
    assert audit_payload["dry_run"] is True
    assert audit_payload["rollback_hint"] == "revert fake changes"
    assert audit_payload["steps"][0]["Authorization"] == "[REDACTED]"
    assert audit_payload["context"]["password"] == "[REDACTED]"

    assert discover.details["api_key"] == "discover-secret"
    assert discover.sanitized()["details"]["api_key"] == "[REDACTED]"
    assert verify.sanitized()["details"]["access_token"] == "[REDACTED]"
    assert status.sanitized()["details"]["token"] == "[REDACTED]"
    assert status.sanitized()["details"]["state"] == "ready"


def test_default_apply_returns_approval_required_contract_placeholder_for_high_risk_plan():
    provider = FakeProvider()
    plan = provider.plan(intent="open-route")

    result = provider.apply(plan=plan)

    assert result.operation == "apply"
    assert result.status is ProviderOperationStatus.PLANNED
    assert result.changed is False
    assert result.dry_run is True
    assert result.approval_required is True
    assert result.details["provider_id"] == "fake"
    assert result.details["risk"] == "high"
    assert result.details["plan"]["context"]["password"] == "[REDACTED]"
    assert result.metadata["placeholder"] is True
    assert result.metadata["not_executable"] is True


def test_default_apply_and_rollback_require_explicit_approval_reference_to_exit_pending_state():
    provider = FakeProvider()
    plan = provider.plan(intent="approved-change")

    pending_result = provider.apply(plan=plan, config={"audit_event_id": "audit_123"})
    apply_result = provider.apply(plan=plan, config={"approval_request_id": "appr_123", "audit_event_id": "audit_123"})
    rollback_result = provider.rollback(plan=plan, config={"approval_request_id": "appr_123", "audit_event_id": "audit_123"})

    assert pending_result.status is ProviderOperationStatus.PLANNED
    assert pending_result.approval_required is True

    assert apply_result.status is ProviderOperationStatus.NOT_IMPLEMENTED
    assert apply_result.approval_required is False
    assert apply_result.changed is False
    assert apply_result.dry_run is True
    assert apply_result.details["approval_request_id"] == "appr_123"
    assert apply_result.metadata["audit_event_id"] == "audit_123"
    assert apply_result.metadata["placeholder"] is True
    assert apply_result.metadata["not_executable"] is True

    assert rollback_result.operation == "rollback"
    assert rollback_result.status is ProviderOperationStatus.NOT_IMPLEMENTED
    assert rollback_result.approval_required is False
    assert rollback_result.changed is False
    assert rollback_result.dry_run is True


def test_fake_provider_satisfies_runtime_protocol():
    provider = FakeProvider()

    assert isinstance(provider, ManagedProviderProtocol)


def test_incomplete_provider_cannot_be_instantiated():
    class IncompleteProvider(ManagedProvider):
        provider_id = "incomplete"
        display_name = "Incomplete Provider"

    try:
        IncompleteProvider()
    except TypeError as exc:
        assert "abstract" in str(exc)
    else:
        raise AssertionError("Incomplete provider should remain abstract")


def test_redact_sensitive_data_covers_nested_dicts_and_strings():
    payload = {
        "nested": {"api_key": "xyz"},
        "Authorization": "Bearer very-secret",
        "safe": "ok",
    }

    assert redact_sensitive_data(payload) == {
        "nested": {"api_key": "[REDACTED]"},
        "Authorization": "[REDACTED]",
        "safe": "ok",
    }
    assert redact_sensitive_data("refresh_token=secret-value password=hunter2") == "[REDACTED] [REDACTED]"
