"""Rubric point 5: generated code must not be able to reach the network."""
from app.tools.sandbox import probe, run_python


def test_basic_execution():
    r = run_python("print(6*7)")
    assert r.ok and "42" in r.stdout


def test_network_is_unreachable():
    r = run_python(
        "import socket\n"
        "socket.setdefaulttimeout(4)\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53))\n"
        "    print('REACHED')\n"
        "except Exception as e:\n"
        "    print('BLOCKED', type(e).__name__)\n")
    assert "REACHED" not in r.stdout, "sandbox leaked network access"
    assert "BLOCKED" in r.stdout


def test_isolation_backend_available():
    caps = probe()
    assert caps["docker"] or caps["netns"], "no isolation backend on this host"
