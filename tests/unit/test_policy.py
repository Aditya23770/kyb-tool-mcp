from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from kyb_tool_mcp.config import load_policy
from kyb_tool_mcp.evidence import EvidenceStore, validate_flags
from kyb_tool_mcp.policy import DECISION_RANK, decide, explain_policy
from kyb_tool_mcp.schemas.decision import Flag, Severity
from kyb_tool_mcp.schemas.evidence import Evidence

POLICY = load_policy()


def f(sev: str, code: str = "X") -> Flag:
    return Flag(code=code, severity=Severity(sev), detail="d", evidence=["ev_1"])


def test_no_flags_approves() -> None:
    o = decide([], POLICY)
    assert (o.decision, o.risk_level, o.score) == ("APPROVE", "LOW", 0)


def test_critical_rejects() -> None:
    assert decide([f("critical")], POLICY).decision == "REJECT"
    assert decide([f("critical")], POLICY).risk_level == "HIGH"


def test_high_escalates() -> None:
    assert decide([f("high")], POLICY).decision == "ESCALATE"


def test_score_threshold_escalates() -> None:
    assert decide([f("medium")], POLICY).decision == "APPROVE"  # 15
    o = decide([f("medium"), f("medium")], POLICY)  # 30
    assert o.decision == "ESCALATE" and o.score == 30


def test_low_flags_approve_with_risk_levels() -> None:
    assert decide([f("low")], POLICY).risk_level == "LOW"
    assert decide([f("medium")], POLICY).risk_level == "MEDIUM"


def test_explain_policy_mentions_rules() -> None:
    text = explain_policy(POLICY)
    assert "REJECT" in text and "SANCTIONS_MATCH" in text and POLICY.version in text


severities = st.sampled_from(["critical", "high", "medium", "low"])
flag_lists = st.lists(severities.map(f), max_size=12)


@given(flag_lists)
def test_property_critical_never_approves(flags: list[Flag]) -> None:
    flags = [*flags, f("critical", "SANCTIONS_MATCH")]
    assert decide(flags, POLICY).decision == "REJECT"


@given(flag_lists, severities)
def test_property_adding_flag_never_softens(flags: list[Flag], extra: str) -> None:
    before = DECISION_RANK[decide(flags, POLICY).decision]
    after = DECISION_RANK[decide([*flags, f(extra)], POLICY).decision]
    assert after >= before


@given(flag_lists)
def test_property_deterministic_and_order_independent(flags: list[Flag]) -> None:
    assert decide(flags, POLICY) == decide(list(reversed(flags)), POLICY)


def test_evidence_validator_drops_unknown_ids() -> None:
    store = EvidenceStore()
    ev = Evidence[dict[str, int]].build(provider="p", kind="k", request={}, payload={"a": 1})
    store.add(ev)
    good = Flag(code="A", severity=Severity.high, detail="", evidence=[ev.id])
    bad = Flag(code="B", severity=Severity.high, detail="", evidence=["ev_missing"])
    empty = Flag(code="C", severity=Severity.high, detail="", evidence=[])
    exempt = Flag(code="CHECK_NOT_CONFIGURED", severity=Severity.low, detail="", evidence=[])
    valid, rejected = validate_flags([good, bad, empty, exempt], store)
    assert [x.code for x in valid] == ["A", "CHECK_NOT_CONFIGURED"]
    assert [x.code for x in rejected] == ["B", "C"]


def test_evidence_ids_are_deterministic() -> None:
    a = Evidence[dict[str, int]].build(provider="p", kind="k", request={"q": 1}, payload={"a": 1})
    b = Evidence[dict[str, int]].build(provider="p", kind="k", request={"q": 1}, payload={"a": 1})
    c = Evidence[dict[str, int]].build(provider="p", kind="k", request={"q": 2}, payload={"a": 1})
    assert a.id == b.id != c.id
