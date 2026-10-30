"""Demo ingestion must use the same protected original storage as uploads."""
from pathlib import Path
from unittest.mock import MagicMock

from scripts.demo_wave import ingest_demo_corpus as demo
from scripts.demo_wave.generate_demo_assets import generate_corpus


def test_demo_job_reads_a_protected_copy_and_keeps_the_corpus(tmp_path, monkeypatch):
    corpus = tmp_path / "demo"
    records = generate_corpus(corpus)
    source = corpus / records[0]["path"]
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = None
    connection.execute.return_value.mappings.return_value.first.return_value = {
        "KnowledgeOwnerUserID": 1, "KnowledgeApproverUserID": 2,
        "TaxonomyVersion": "v1",
    }
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value = connection
    monkeypatch.setattr(demo, "engine", engine)
    monkeypatch.setattr(demo, "ROOT", tmp_path)
    monkeypatch.setattr(demo, "_ensure_engine", lambda: None)
    jobs = []
    monkeypatch.setattr(demo, "create_ingestion_job", lambda **kw: jobs.append(kw))

    demo.ingest_corpus(corpus, limit=1)

    original = Path(jobs[0]["file_path"]).resolve()
    assert original.is_relative_to(tmp_path / "data" / "raw" / "Uploads")
    assert original != source and original.read_bytes() == source.read_bytes()
    assert jobs[0]["file_name"] == source.name
    assert source.is_file()
