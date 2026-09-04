from pathlib import Path


def test_dockerfile_copies_shared_contract_package():
    service_root = Path(__file__).resolve().parents[1]
    dockerfile = (service_root / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY packages/contracts /tmp/jobgraph-contracts" in dockerfile
    assert "COPY services/cv-extraction /app/cv-extraction" in dockerfile
    assert "COPY services/jd-extraction" not in dockerfile
