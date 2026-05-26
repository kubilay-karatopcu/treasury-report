"""Sunum patch-validator scope enforcement (spec §4.1) + backwards compat."""
from __future__ import annotations

from presentations.graph import GraphState
from presentations.nodes.validate_patch import _check_scope_contract, validate_patch


def _manifest_with_bindings():
    return {
        "id": "p_abc123", "version": 7,
        "scope_ref": {"presentation_id": "p_abc123", "scope_version": 4},
        "meta": {}, "blocks": [{
            "id": "sec", "type": "section_header", "title": "S", "children": [{
                "id": "b1", "type": "kpi", "title": "K",
                "variable_bindings": {
                    "as_of_from": {"from_scope_filter": "pf_q4_2025", "accessor": "from"},
                    "currency_list": {"from_scope_filter": "if_currency"},
                },
            }],
        }],
    }


PIN_ERR = "Cannot mutate pinned filter 'pf_q4_2025' — set in scope contract scope_v4"


class TestCheckScopeContract:
    def test_direct_pinned_mutation_rejected(self, sample_scope):
        patches = [{"op": "replace", "path": "/filters/pinned/pf_q4_2025/from",
                    "value": "2020-01-01"}]
        errors = _check_scope_contract(_manifest_with_bindings(), patches, sample_scope)
        assert errors == [PIN_ERR]

    def test_pinned_bound_variable_mutation_rejected(self, sample_scope):
        patches = [{"op": "replace",
                    "path": "/blocks/0/children/0/variable_bindings/as_of_from",
                    "value": {"constant": "today"}}]
        errors = _check_scope_contract(_manifest_with_bindings(), patches, sample_scope)
        assert errors == [PIN_ERR]

    def test_interactive_bound_variable_mutation_allowed(self, sample_scope):
        patches = [{"op": "replace",
                    "path": "/blocks/0/children/0/variable_bindings/currency_list",
                    "value": {"constant": "TRY"}}]
        errors = _check_scope_contract(_manifest_with_bindings(), patches, sample_scope)
        assert errors == []

    def test_scope_ref_tampering_rejected(self, sample_scope):
        patches = [{"op": "replace", "path": "/scope_ref/scope_version", "value": 99}]
        errors = _check_scope_contract(_manifest_with_bindings(), patches, sample_scope)
        assert len(errors) == 1 and "scope_ref" in errors[0]

    def test_scope_reentry_flag_bypasses(self, sample_scope):
        patches = [{"op": "replace", "path": "/scope_ref/scope_version", "value": 99,
                    "_scope_reentry": True}]
        assert _check_scope_contract(_manifest_with_bindings(), patches, sample_scope) == []

    def test_routing_coercion_rejected(self, sample_scope):
        patches = [{"op": "add", "path": "/status/cached_tables/-", "value": "big"}]
        errors = _check_scope_contract(_manifest_with_bindings(), patches, sample_scope)
        assert len(errors) == 1 and "routing" in errors[0].lower()

    def test_normal_block_edit_allowed(self, sample_scope):
        patches = [{"op": "replace", "path": "/blocks/0/children/0/title", "value": "New"}]
        assert _check_scope_contract(_manifest_with_bindings(), patches, sample_scope) == []

    def test_no_scope_contract_skips_all_checks(self):
        # Even a blatant pinned mutation passes when there is no scope contract.
        patches = [{"op": "replace", "path": "/filters/pinned/pf_q4_2025/from", "value": "x"}]
        assert _check_scope_contract(_manifest_with_bindings(), patches, None) == []


class TestValidatePatchIntegration:
    def test_pinned_mutation_surfaces_in_validation_errors(self, sample_scope):
        state = GraphState(
            presentation_id="p_abc123",
            manifest=_manifest_with_bindings(),
            user_message="change the date",
            scope_contract=sample_scope,
            pending_patches=[{"op": "replace", "path": "/filters/pinned/pf_q4_2025/from",
                              "value": "2020-01-01"}],
        )
        out = validate_patch(state)
        assert PIN_ERR in out.validation_errors

    def test_backwards_compat_no_scope(self):
        # No scope_ref, no scope_contract → only the existing (non-scope) checks run.
        manifest = {"id": "p", "version": 1, "meta": {}, "blocks": [{
            "id": "sec", "type": "section_header", "title": "S", "children": [],
        }]}
        state = GraphState(
            presentation_id="p", manifest=manifest, user_message="rename",
            pending_patches=[{"op": "replace", "path": "/blocks/0/title", "value": "X"}],
        )
        out = validate_patch(state)
        assert out.validation_errors == []
