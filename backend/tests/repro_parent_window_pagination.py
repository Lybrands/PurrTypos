"""Known candidate blocker. Run explicitly; not a passing acceptance test.

A completed first stage exceeds the repository's 200-event page. The Core
reads only that page when reconciling delivery and mistakes it for unresolved.
No host pagination limit is relaxed here.
"""
import pytest
from tests import test_parent_result_window as window


@pytest.mark.asyncio
async def test_completed_long_stage_must_not_be_treated_as_unresolved(tmp_path):
    await window.test_stage_reaches_frontend_before_slow_child_and_root_finish(
        tmp_path, 'done', extra_stage_chunks=240,
    )
