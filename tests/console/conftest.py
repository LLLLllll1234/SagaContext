import pytest
from scripts.console_fixture import create_fixture
from sagacontext.console.service import ConsoleReadService


@pytest.fixture
def console_case(tmp_path):
    case=create_fixture(tmp_path)
    case.service=ConsoleReadService(case.path,case.owner_a)
    return case
