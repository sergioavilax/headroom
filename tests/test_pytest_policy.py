"""Guards on "keyless by default" (BUILD_PLAN §0.2 invariant 4).

Every test in this repo runs without an API key. Tests that spend money carry the
`live` marker and are excluded from every default collection. That policy lives in
`pyproject.toml`, which means it can be edited away by accident — so it is pinned
here three ways: the marker is registered, the default addopts deselect it, and a
live-marked test really is deselected when the committed configuration is applied.
"""

import shlex

import pytest


def test_live_marker_is_registered(pytestconfig: pytest.Config) -> None:
    declared = [line.split(":", 1)[0].strip() for line in pytestconfig.getini("markers")]
    assert "live" in declared, f"the `live` marker must stay declared; found {declared}"


def test_default_addopts_deselect_live(pytestconfig: pytest.Config) -> None:
    addopts: list[str] = pytestconfig.getini("addopts")
    assert "-m" in addopts, f"addopts must pin a marker expression; found {addopts}"
    assert any("not live" in opt for opt in addopts), (
        f"the default marker expression must exclude `live`; found {addopts}"
    )


def test_live_test_is_deselected_under_the_committed_config(
    pytester: pytest.Pytester, pytestconfig: pytest.Config
) -> None:
    """Replay this repo's own ini settings against a live-marked test."""
    addopts = " ".join(shlex.quote(opt) for opt in pytestconfig.getini("addopts"))
    markers = "\n".join(f"    {line}" for line in pytestconfig.getini("markers"))
    pytester.makeini(f"[pytest]\naddopts = {addopts}\nmarkers =\n{markers}\n")
    pytester.makepyfile(
        """
        import pytest


        def test_keyless_runs():
            assert True


        @pytest.mark.live
        def test_costs_money():
            raise AssertionError("a live test must never run in a default collection")
        """
    )

    result = pytester.runpytest_subprocess()

    result.assert_outcomes(passed=1, deselected=1)
