"""
audit.py - the adapter's own audit log: one JSON line per call, hash-chained. No Qt.

Each record carries `prev` (hash of the record before) and `hash` = sha256(prev + canonical JSON of
the record without `hash`), so editing or deleting a line breaks the chain from that point on
(`verify`). Files are per month: <audit dir>/YYYY-MM.jsonl; the chain continues across files.

Record: {seq, time, client, profile, via, tool, args, rows, ms, result: ok | error | blocked |
truncated, error?, prev, hash}. Optional: forward each record to a SIEM over syslog (UDP).
"""
from __future__ import annotations

import hashlib
import json
import socket
import threading
import time
from collections import deque
from pathlib import Path

GENESIS = "0" * 64


def _canon(rec: dict) -> bytes:
    return json.dumps({k: v for k, v in rec.items() if k != "hash"}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")


def record_hash(prev: str, rec: dict) -> str:
    return hashlib.sha256(prev.encode("ascii") + _canon(rec)).hexdigest()


class AuditLog:
    def __init__(self, folder: str | Path, syslog: tuple[str, int] | None = None, keep: int = 500):
        self.folder = Path(folder)
        self.syslog = syslog if syslog and syslog[0] else None
        self.recent: deque = deque(maxlen=keep)      # newest last, for the UI
        self._lock = threading.Lock()
        self._sock = None
        self.last_hash, self.seq = GENESIS, 0
        self._load_tail()

    # ---- files
    def files(self) -> list[Path]:
        return sorted(self.folder.glob("*.jsonl")) if self.folder.is_dir() else []

    def _load_tail(self):
        for f in self.files():
            for rec in self._read(f):
                self.recent.append(rec)
                self.last_hash, self.seq = rec.get("hash", self.last_hash), rec.get("seq", self.seq)

    @staticmethod
    def _read(f: Path):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            yield {"_corrupt": line}
        except OSError:
            return

    # ---- write
    def write(self, **rec) -> dict:
        with self._lock:
            self.seq += 1
            rec = {"seq": self.seq, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec, "prev": self.last_hash}
            rec["hash"] = record_hash(self.last_hash, rec)
            self.last_hash = rec["hash"]
            self.folder.mkdir(parents=True, exist_ok=True)
            f = self.folder / (time.strftime("%Y-%m") + ".jsonl")
            with open(f, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            self.recent.append(rec)
        self._forward(rec)
        return rec

    def _forward(self, rec: dict):
        if not self.syslog:
            return
        try:
            if self._sock is None:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            msg = f"<134>1 {rec['time']} {socket.gethostname()} mcx-adapter - audit - " + json.dumps(rec, default=str)
            self._sock.sendto(msg.encode("utf-8")[:8000], self.syslog)
        except OSError:
            pass                                    # the file is the record; syslog is best effort

    # ---- check
    def verify(self) -> tuple[bool, int, str]:
        """(ok, records checked, message). Walks every file in order."""
        prev, n = GENESIS, 0
        for f in self.files():
            for rec in self._read(f):
                if "_corrupt" in rec:
                    return False, n, f"{f.name}: unreadable line after record {n}"
                if rec.get("prev") != prev or record_hash(prev, rec) != rec.get("hash"):
                    return False, n, f"{f.name}: chain broken at record seq {rec.get('seq')}"
                prev, n = rec["hash"], n + 1
        return True, n, f"{n} records, chain verified"

    def today(self) -> list[dict]:
        day = time.strftime("%Y-%m-%d")
        return [r for r in self.recent if str(r.get("time", "")).startswith(day)]

    def export_csv(self, path: str | Path):
        import csv
        cols = ["seq", "time", "client", "profile", "via", "tool", "args", "rows", "ms", "result", "error", "hash"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for f in self.files():
                for r in self._read(f):
                    w.writerow([json.dumps(r.get(c), default=str) if c == "args" else r.get(c, "") for c in cols])
