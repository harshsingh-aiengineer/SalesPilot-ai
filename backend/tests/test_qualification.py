from app.models import Lead
from app.services.qualification import apply_llm_interpretation, rule_qualify, source_facts


def _lead(**kwargs) -> Lead:
    data = dict(
        name="Aisha Rahman",
        email="aisha.rahman.example@example.com",
        requirements="Motorized blinds for a bedroom",
        location="Dubai Marina",
        currency="AED",
        notes="",
    )
    data.update(kwargs)
    return Lead(**data)


def test_rules_do_not_invent_budget_or_measurements():
    result = rule_qualify(_lead())
    assert result.budget_min is None
    assert "budget" in result.missing_fields
    assert "measurements" in result.missing_fields
    assert result.method == "rules"
    assert result.priority in {"hot", "warm", "cold"}


def test_model_budget_is_rejected_when_it_is_not_in_the_source():
    base = rule_qualify(_lead())
    facts = source_facts("Motorized blinds for a bedroom", "Dubai Marina")
    merged = apply_llm_interpretation(
        base,
        {
            "budget_min": 99999,
            "budget_max": 150000,
            "deadline": "next Tuesday",
            "priority": "hot",
            "score": 99,
            "explanation": "They have a large budget.",
        },
        facts,
    )
    assert merged.budget_min is None
    assert merged.budget_max is None
    assert merged.deadline is None
    assert merged.priority != "hot"
    assert any("budget_min" in note or "Ignored" in note for note in merged.uncertainty_notes)


def test_model_budget_is_kept_when_the_source_contains_it():
    base = rule_qualify(_lead(requirements="Motorized blinds, budget 8000 AED", budget_min=None))
    facts = source_facts("Motorized blinds, budget 8000 AED")
    merged = apply_llm_interpretation(base, {"budget_min": 8000, "currency": "AED"}, facts)
    assert merged.budget_min == 8000
