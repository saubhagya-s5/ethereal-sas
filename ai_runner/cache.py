"""Raw answer and extraction cache under output/raw/. A cached file is never re-requested."""
from __future__ import annotations

import json
from pathlib import Path


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class RawCache:
    def __init__(self, root: Path):
        self.root = root

    def answer_path(self, engine: str, query_id: str, run: int) -> Path:
        return self.root / engine / f"{query_id}_run{run}.json"

    def extract_path(self, engine: str, query_id: str, run: int) -> Path:
        return self.root / "_extract" / engine / f"{query_id}_run{run}.json"

    def get_answer(self, engine: str, query_id: str, run: int) -> dict | None:
        p = self.answer_path(engine, query_id, run)
        return read_json(p) if p.exists() else None

    def put_answer(self, engine: str, query_id: str, run: int, record: dict) -> Path:
        p = self.answer_path(engine, query_id, run)
        write_json(p, record)
        return p

    def get_extract(self, engine: str, query_id: str, run: int) -> dict | None:
        p = self.extract_path(engine, query_id, run)
        return read_json(p) if p.exists() else None

    def put_extract(self, engine: str, query_id: str, run: int, record: dict) -> None:
        write_json(self.extract_path(engine, query_id, run), record)
