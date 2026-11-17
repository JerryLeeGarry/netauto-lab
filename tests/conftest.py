import pytest
from helpers import SOT_FILE

from netauto.model import load_sot


@pytest.fixture
def sot():
    return load_sot(SOT_FILE)
