from __future__ import annotations

import pytest

from services.screenplay_pdf import build_screenplay_pdf


def test_screenplay_pdf_builds_title_and_body_pages():
    pytest.importorskip("reportlab")

    data = build_screenplay_pdf(
        title="Fog Harbor",
        screenplay_format="Feature",
        content=(
            "INT. RADIO STATION - NIGHT\n\n"
            "@LIN\n"
            "(quietly)\n"
            "Are you still there?\n\n"
            "CUT TO:"
        ),
    )

    assert data.startswith(b"%PDF-")
    assert len(data) > 2_000
    assert data.count(b"/Type /Page") >= 2
