# tests/test_import_contracts.py
"""R3 (isolation non-contournable) : aucun module hors de `velmo.memory` ne doit
importer `velmo.memory.db` directement — seule l'API publique de `velmo.memory`
(MemoryManager) et `velmo.memory.episodic`/`velmo.memory.retention` (déjà dans le
package) y ont accès. Contrat vérifié par `import-linter` (voir pyproject.toml)."""

from __future__ import annotations

import subprocess


def test_lint_imports_contract_passes() -> None:
    result = subprocess.run(
        ["lint-imports", "--config", "pyproject.toml"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
