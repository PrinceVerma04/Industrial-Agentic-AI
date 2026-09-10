from app.core.audit import AuditLog


def test_chain_verifies(tmp_path):
    a = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        a.append("event", i=i)
    assert a.verify() == (True, None)


def test_tampering_is_detected(tmp_path):
    p = tmp_path / "audit.jsonl"
    a = AuditLog(p)
    for i in range(4):
        a.append("event", i=i)
    lines = p.read_text().splitlines()
    lines[2] = lines[2].replace('"i": 2', '"i": 999')
    p.write_text("\n".join(lines) + "\n")
    ok, bad = AuditLog(p).verify()
    assert not ok and bad == 2
