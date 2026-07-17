from domains.writing.tools.setting_helpers import (
    filter_setting_rows,
    fields_from_args,
    merge_setting_proposal,
    send_setting_updated,
    setting_snapshot,
)


def test_fields_from_args_keeps_only_explicit_writable_values():
    assert fields_from_args({
        "name": "Lin",
        "tags": None,
        "profileMd": "# Profile",
        "ignored": "value",
    }) == {
        "name": "Lin",
        "profile_md": "# Profile",
    }


def test_merge_setting_proposal_preserves_unspecified_fields():
    current = {"name": "Lin", "tags": "lead", "profile_md": "old"}

    assert setting_snapshot(current) == {
        "name": "Lin",
        "tags": "lead",
        "profileMd": "old",
    }
    assert merge_setting_proposal(current, {"profile_md": "new"}) == {
        "name": "Lin",
        "tags": "lead",
        "profileMd": "new",
    }


def test_send_setting_updated_is_optional_and_uses_a_stable_envelope():
    chunks = []
    send_setting_updated(None, "character", action="create")
    send_setting_updated(chunks.append, "entity", action="delete", id=7)

    assert chunks == [{
        "settingUpdated": {
            "kind": "entity",
            "action": "delete",
            "id": 7,
        },
    }]


def test_filter_setting_rows_normalizes_ids_and_ignores_invalid_values():
    rows = [
        {"id": 1, "name": "Lin"},
        {"id": 2, "name": "Mara"},
    ]

    assert filter_setting_rows(rows, ids=["2", "invalid"]) == [rows[1]]


def test_filter_setting_rows_matches_partial_names_case_insensitively():
    rows = [
        {"id": 1, "name": "North Harbor"},
        {"id": 2, "name": "South Gate"},
    ]

    assert filter_setting_rows(rows, names=["HARB"]) == [rows[0]]
    assert filter_setting_rows(rows, ids=[1], names=["south"]) == [rows[0]]
