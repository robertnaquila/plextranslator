import pytest

from plextranslator.pipeline import Chunk, plan_chunks


def test_plan_chunks_even_split():
    chunks = plan_chunks(120, 60)
    assert chunks == [Chunk(0, 60), Chunk(60, 60)]


def test_plan_chunks_last_chunk_clamped():
    chunks = plan_chunks(100, 60)
    assert chunks[-1] == Chunk(60, 40)
    assert chunks[-1].end == 100


def test_plan_chunks_with_start():
    chunks = plan_chunks(120, 60, start=90)
    assert chunks == [Chunk(90, 30)]


def test_plan_chunks_nothing_to_do():
    assert plan_chunks(60, 60, start=60) == []
    assert plan_chunks(0, 60) == []


def test_plan_chunks_rejects_nonpositive():
    with pytest.raises(ValueError):
        plan_chunks(60, 0)
