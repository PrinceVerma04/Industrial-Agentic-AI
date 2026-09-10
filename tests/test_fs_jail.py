import pytest
from app.tools import fs


def test_write_read_roundtrip():
    fs.write_file("t/a.txt", "hello")
    assert fs.read_file("t/a.txt") == "hello"


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "t/../../../x"])
def test_traversal_blocked(bad):
    with pytest.raises((PermissionError, FileNotFoundError)):
        fs.read_file(bad)
