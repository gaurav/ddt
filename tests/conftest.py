from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples"


@pytest.fixture
def henderson_raw() -> bytes:
    return (FIXTURES / "henderson_excerpt.csv").read_bytes()


@pytest.fixture
def cushman_raw() -> bytes:
    return (FIXTURES / "cushman_excerpt.csv").read_bytes()
