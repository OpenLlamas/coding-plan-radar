from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # make tests work without an editable install too
    sys.path.insert(0, str(SRC))

from coding_plan_radar.config import Vendor  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def vendor_a() -> Vendor:
    return Vendor(
        id="vendor_a",
        name="Vendor A",
        region="global",
        currency="USD",
        pricing_url="https://acme.test/pricing",
    )


@pytest.fixture
def vendor_b() -> Vendor:
    return Vendor(
        id="vendor_b",
        name="Vendor B",
        region="cn",
        currency="CNY",
        pricing_url="https://example.test/pricing",
    )


@pytest.fixture
def page_a() -> str:
    return (FIXTURES / "vendor_a.html").read_text(encoding="utf-8")


@pytest.fixture
def page_b() -> str:
    return (FIXTURES / "vendor_b.html").read_text(encoding="utf-8")
