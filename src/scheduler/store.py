"""运行目录持久化：state.json / artifacts/*.json / events.jsonl。

唯一允许读写 runs/ 的地方（architecture §8.5 禁止事项 1）。所有写操作原子替换，
保证进程被 kill 后 state.json 不会出现半截 JSON（§3.4）。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .context import RunEvent, RunState


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)  # 原子替换
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class RunStore:
    """一次运行对应 runs/<run_id>/ 一个目录。"""

    def __init__(self, root: str | Path = "runs") -> None:
        self.root = Path(root)

    # ------------------------------------------------------------ 路径
    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "state.json"

    def artifacts_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "artifacts"

    def events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    def config_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "config.json"

    def ensure(self, run_id: str) -> None:
        self.artifacts_dir(run_id).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ state
    def load_state(self, run_id: str) -> RunState | None:
        path = self.state_path(run_id)
        if not path.exists():
            return None
        return RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_state(self, state: RunState) -> None:
        state.touch()
        _atomic_write_text(
            self.state_path(state.run_id),
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
        )

    # ------------------------------------------------------------ config
    def save_config(self, run_id: str, config: dict[str, Any]) -> None:
        _atomic_write_text(self.config_path(run_id), json.dumps(config, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------ artifacts
    def write_artifacts(self, run_id: str, artifacts: dict[str, Any]) -> dict[str, str]:
        """落盘本阶段全部产物，返回 {artifact_name: 相对路径}。"""
        index: dict[str, str] = {}
        for name, payload in artifacts.items():
            path = self.artifacts_dir(run_id) / f"{name}.json"
            _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
            index[name] = path.as_posix()
        return index

    def load_artifact(self, run_id: str, name: str, index: dict[str, str] | None = None) -> dict[str, Any] | None:
        rel = (index or {}).get(name)
        path = Path(rel) if rel else self.artifacts_dir(run_id) / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def load_artifacts(
        self, run_id: str, names: Iterable[str], index: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """只读快照：只返回 flow.yaml 声明的 input_artifacts，缺的就不放进去（由闸门/校验兜底）。"""
        snapshot: dict[str, Any] = {}
        for name in names:
            art = self.load_artifact(run_id, name, index)
            if art is not None:
                snapshot[name] = art
        return snapshot

    # ------------------------------------------------------------ events
    def append_event(self, event: RunEvent) -> None:
        path = self.events_path(event.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def read_events(self, run_id: str) -> list[dict[str, Any]]:
        path = self.events_path(run_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def read_artifact_names(self, run_id: str) -> list[str]:
        d = self.artifacts_dir(run_id)
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.json"))
