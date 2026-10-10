from ipo_agents.ingestion import ingest


def test_every_page_is_assigned_to_a_section(tiny_rhp):
    doc = ingest(tiny_rhp)

    assert doc.page_count == 3
    assert doc.coverage == 1.0
    assigned = sorted(p for s in doc.sections.values() for c in s for p in range(c.page_start, c.page_end + 1))
    assert assigned == [1, 2, 3]


def test_chunks_keep_page_text(tiny_rhp):
    doc = ingest(tiny_rhp)

    texts = [c.text for s in doc.sections.values() for c in s]
    assert any("Rs. 1,250.00 crore" in t for t in texts)
