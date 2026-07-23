from __future__ import annotations

from velmo.mlops.stats import non_regression_ok


def test_non_regression_ok_when_scores_stable() -> None:
    baseline = [0.9, 0.9, 0.9, 0.9, 0.9]
    current = [0.9, 0.9, 0.9, 0.9, 0.9]
    assert non_regression_ok(baseline, current) is True


def test_non_regression_fails_when_current_drops_far_below_baseline() -> None:
    baseline = [0.9, 0.91, 0.89, 0.9, 0.9]
    current = [0.3, 0.28, 0.32, 0.29, 0.31]
    assert non_regression_ok(baseline, current) is False


def test_non_regression_tolerates_small_variance_within_2_sigma() -> None:
    baseline = [0.85, 0.9, 0.88, 0.92, 0.87]  # écart-type non nul
    current = [0.86, 0.89, 0.87, 0.9, 0.88]  # variation mineure, dans le bruit
    assert non_regression_ok(baseline, current) is True
