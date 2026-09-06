"""Attach the business method for an already resolved screenplay step."""

from pathlib import Path

from application.screenplay_part_contracts import PART_CONTRACTS, ScreenplayPartContract


_SKILLS_DIR = (
    Path(__file__).resolve().parents[1] / "domains" / "screenplay_agent" / "skills"
)


def with_screenplay_step_skill(
    contract: ScreenplayPartContract,
    instruction: str,
) -> str:
    if PART_CONTRACTS.get(contract.key) != contract:
        raise ValueError("screenplay_part_contract_unknown")
    method = (_SKILLS_DIR / contract.key / "SKILL.md").read_text(
        encoding="utf-8",
    ).strip()
    if not method:
        raise ValueError(f"screenplay step skill is empty: {contract.key}")
    return f"{method}\n\n{instruction}"
