"""Tests for the Recall query engine — one per query type, run against
the deterministic fixtures.  Plain asserts: runnable under pytest OR via
tests/run_tests.py (no pytest install required).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from tubelive import StreamMemory, ask  # noqa: E402

GTA = ROOT / "fixtures" / "gta_rp_demo" / "memory.json"
VAL = ROOT / "fixtures" / "valorant_demo" / "memory.json"


def gta() -> StreamMemory:
    return StreamMemory.load(GTA)


def val() -> StreamMemory:
    return StreamMemory.load(VAL)


def test_fixtures_are_rich():
    m = gta()
    assert m.video.duration_s == 2400
    assert len(m.records) >= 90, len(m.records)
    assert {r.kind for r in m.records} == {
        "scene", "transcript", "chat", "stream_event", "game_event"}
    assert m.tracks and m.entities


def test_store_roundtrip_and_helpers():
    m = gta()
    jp = m.resolve_entity("JP")            # alias resolution
    assert jp is not None and jp.name == "Jean Paul"
    assert m.by_window(900, 1200, kinds={"scene"})
    assert len(m.by_entity(jp, after=840)) > 10
    assert m.by_type("sub", kind="stream_event")
    assert m.search("bank robbery", kinds={"scene"})


def test_recap_last_5_minutes():
    ans = ask(gta(), "what happened in the last 5 minutes?", now=1200)
    assert ans.query_type == "RECAP_WINDOW"
    assert "chase" in ans.summary.lower(), ans.summary
    assert any(900 <= c.t <= 1200 for c in ans.citations), ans.citations
    assert ans.tool_trace, "trace is a product feature, never empty"


def test_recap_explicit_window():
    ans = ask(gta(), "what happened between 10 and 15 minutes?")
    assert ans.query_type == "RECAP_WINDOW"
    assert "bank" in ans.summary.lower() or "rob" in ans.summary.lower()


def test_recap_first_10_minutes():
    ans = ask(gta(), "what happened in the first 10 minutes?")
    assert ans.query_type == "RECAP_WINDOW"
    assert any(c.t < 600 for c in ans.citations)


def test_entity_after_event():
    ans = ask(gta(), "what happened to Jean Paul after he robbed the bank?")
    assert ans.query_type == "ENTITY_AFTER_EVENT"
    s = ans.summary.lower()
    assert "chase" in s, ans.summary
    assert "marty" in s or "apartments" in s, ans.summary
    assert "paleto" in s, ans.summary           # the stated next plan
    assert ans.citations, "must cite evidence"
    assert all(c.t >= 840 for c in ans.citations), ans.citations
    assert any("entity_timeline" in step for step in ans.tool_trace)


def test_search_moment_bank():
    ans = ask(gta(), "when did Jean Paul rob the bank?")
    assert ans.query_type == "SEARCH_MOMENT"
    assert ans.citations and ans.citations[0].t == 480, ans.citations


def test_search_moment_chase():
    ans = ask(gta(), "when did the chase start?")
    assert ans.query_type == "SEARCH_MOMENT"
    assert ans.citations and ans.citations[0].t == 900, ans.citations


def test_next_is_stated_and_quotes_paleto():
    ans = ask(gta(), "where are they going next?")
    assert ans.query_type == "STATED_PLAN"
    assert ans.source_label == "STATED", ans.summary
    assert "Paleto" in ans.summary, ans.summary
    assert "After this we hit Paleto" in ans.summary, ans.summary
    ans2 = ask(gta(), "what are they doing next?")
    assert ans2.source_label == "STATED" and "Paleto" in ans2.summary


def test_next_without_spoken_plan_is_unknown():
    ans = ask(val(), "where are they going next?")
    assert ans.query_type == "STATED_PLAN"
    assert ans.source_label == "UNKNOWN", ans.summary
    assert ans.status == "insufficient_evidence"
    assert ans.confidence < 0.5


def test_current_activity():
    ans = ask(gta(), "what are they doing right now?", now=1200)
    assert ans.query_type == "CURRENT_ACTIVITY"
    assert "chase" in ans.summary.lower(), ans.summary


def test_game_event_clutch_yes():
    ans = ask(val(), "was there a 1 v 5 win?")
    assert ans.query_type == "GAME_EVENT"
    assert ans.summary.startswith("Yes"), ans.summary
    assert any(c.t == 150 for c in ans.citations), ans.citations
    assert ans.clips and ans.clips[0].t_start <= 150
    ans2 = ask(val(), "was there a clutch?")
    assert ans2.summary.startswith("Yes")


def test_game_event_negative_on_gta():
    ans = ask(gta(), "was there a 1 v 5 win?")
    assert ans.query_type == "GAME_EVENT"
    assert ans.status == "insufficient_evidence", ans.summary
    assert "cannot verify" in ans.summary


def test_count_subs():
    ans = ask(gta(), "how many subs were there?")
    assert ans.query_type == "COUNT"
    assert ans.summary.startswith("6"), ans.summary


def test_count_bank_visits():
    ans = ask(gta(), "how many times did Jean Paul enter the bank?")
    assert ans.query_type == "COUNT"
    assert "1 time" in ans.summary, ans.summary


def test_fallback_grounded_and_honest():
    ans = ask(gta(), "tell me about the money")
    assert ans.query_type == "FALLBACK"
    assert ans.citations
    miss = ask(gta(), "what is the capital of France?")
    assert miss.confidence <= 0.2, miss.summary
