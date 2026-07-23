from __future__ import annotations

from velmo.mlops.versioning import compute_version_hashes, current_git_commit


def test_compute_version_hashes_returns_four_stable_hashes() -> None:
    hashes = compute_version_hashes()
    assert set(hashes) == {
        "prompt_hash",
        "memory_config_hash",
        "guardrail_config_hash",
        "gate_config_hash",
    }
    assert all(len(v) == 64 for v in hashes.values())  # sha256 hex
    # Stable : deux appels successifs sans changement de code produisent le même hash.
    assert compute_version_hashes() == hashes


def test_gate_config_hash_changes_with_threshold(monkeypatch) -> None:
    # D8-05 : les seuils de gate font partie de l'identité de version — en
    # changer un doit changer le hash (conception §Seuils : « chiffres
    # versionnés dans un fichier de config, donc hashés dans la version »).
    before = compute_version_hashes()["gate_config_hash"]
    monkeypatch.setenv("GATE_MIN_SCORE", "0.95")
    after = compute_version_hashes()["gate_config_hash"]
    assert after != before


def test_current_git_commit_returns_short_sha() -> None:
    commit = current_git_commit()
    assert len(commit) >= 7
