from domains.screenplay.project_aggregate import derive_stage, next_actions


def test_incomplete_draft_keeps_the_project_in_draft():
    heads = {
        "creativeBrief": {},
        "structure": {},
        "sceneList": {},
        "screenplayDraft": {"isComplete": False},
    }

    stage = derive_stage(
        source_kind="original",
        head_roles=heads,
        head_contents=heads,
    )

    assert stage == "draft"
    assert next_actions(stage, head_contents=heads) == [{
        "type": "generateDeliverable",
        "targetRole": "screenplayDraft",
    }]


def test_review_requiring_changes_targets_a_new_draft_revision():
    heads = {
        "creativeBrief": {},
        "structure": {},
        "sceneList": {},
        "screenplayDraft": {"isComplete": True},
        "review": {"verdict": "revise"},
    }

    stage = derive_stage(
        source_kind="original",
        head_roles=heads,
        head_contents=heads,
    )

    assert stage == "review"
    assert next_actions(stage, head_contents=heads) == [{
        "type": "generateDeliverable",
        "targetRole": "screenplayDraft",
    }]


def test_ready_review_completes_the_project():
    heads = {
        "creativeBrief": {},
        "structure": {},
        "sceneList": {},
        "screenplayDraft": {"isComplete": True},
        "review": {"verdict": "ready"},
    }

    assert derive_stage(
        source_kind="original",
        head_roles=heads,
        head_contents=heads,
    ) == "completed"
