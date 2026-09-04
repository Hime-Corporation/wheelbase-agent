"""read_file over the desktop relay: .ipynb/.docx/.xlsx must go through
structured-document extraction, not the raw `read` frame.

Bug: _relay_file's raw `read` frame asks the sidecar for `Bun.file(path)
.text()` — a UTF-8 decode. A .docx/.xlsx is a ZIP container, so that
decode returns mojibake instead of readable text. _relay_read_extract fixes
this by routing EXTRACTABLE_EXTENSIONS (tools/read_extract.py) through the
same ShellFileOperations-over-DesktopRelayEnvironment machinery
patch/search_files already use (_relay_file_ops), mirroring
read_file_tool's own extraction stage exactly.

Uses the SAME RealBashTransport pattern as test_file_ops_relay.py: `exec`
frames run as real bash subprocesses against a real tmp_path, so
ShellFileOperations.read_file_bytes' base64-over-exec-frame path is
exercised for real, not just mocked.
"""
from __future__ import annotations

import importlib
import json
import queue
import subprocess
import zipfile

import pytest

plug = importlib.import_module("plugins.wheelbase-desktop-exec")
transport_mod = importlib.import_module("plugins.wheelbase-desktop-exec.transport")


class RealBashTransport(transport_mod.ExecTransport):
    """Runs `exec` frames as real bash subprocesses (mirrors
    test_file_ops_relay.py's transport of the same name)."""

    def __init__(self, connected=True):
        self.sent = []
        self._connected = connected
        self._q: dict[str, "queue.Queue[dict]"] = {}

    def send(self, frame):
        if not self._connected:
            raise transport_mod.PreDispatchError("no relay")
        self.sent.append(dict(frame))
        rid = frame["request_id"]
        q = self._q.setdefault(rid, queue.Queue())
        if frame["type"] == "exec":
            proc = subprocess.run(
                ["bash", "-c", frame["command"]],
                capture_output=True, text=True,
            )
            q.put({"type": "chunk", "data": (proc.stdout or "") + (proc.stderr or "")})
            q.put({"type": "exit", "exit_code": proc.returncode})

    def recv(self, request_id, timeout=None):
        return self._q.setdefault(request_id, queue.Queue()).get(timeout=timeout or 10)

    def close(self):
        pass


class RawFrameTransport(transport_mod.ExecTransport):
    """Scripts a `read` frame reply — used to prove the raw fast path is
    still the one taken for non-extractable extensions."""

    def __init__(self, data):
        self.sent = []
        self._data = data

    def send(self, frame):
        self.sent.append(dict(frame))

    def recv(self, request_id, timeout=None):
        return {"type": "result", "data": self._data}

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _ident(tmp_path):
    # cwd MUST be a real, existing directory — DesktopRelayEnvironment's
    # wrapped script does `builtin cd -- <cwd> || exit 126` before every
    # command (see test_file_ops_relay.py's identical fixture note).
    from wheelbase_sdk import runtime
    runtime.set_task_identity("t-desk", {"user_id": "u", "shell_relay_url": "wss://relay",
                                         "workspace_root": str(tmp_path), "cwd": str(tmp_path)})
    yield
    runtime._current.set(None)
    with runtime._lock:
        runtime._by_task.clear()


def _no_next():
    def nc(args):
        raise AssertionError("next_call must NOT run — the desktop relay must have handled it")
    return nc


_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _write_docx(path, document_xml):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", document_xml)


def test_is_extractable_read_matches_read_extract_extensions():
    assert plug._is_extractable_read({"path": "a.docx"}) is True
    assert plug._is_extractable_read({"path": "a.XLSX"}) is True
    assert plug._is_extractable_read({"path": "a.ipynb"}) is True
    assert plug._is_extractable_read({"path": "a.txt"}) is False
    assert plug._is_extractable_read({"path": "a.py"}) is False


def test_read_file_docx_over_relay_extracts_text_not_mojibake(monkeypatch, tmp_path):
    f = tmp_path / "report.docx"
    _write_docx(
        str(f),
        (f'<?xml version="1.0"?><w:document xmlns:w="{_NS_W}">'
         '<w:body><w:p><w:r><w:t>Quarterly report body</w:t></w:r></w:p>'
         '</w:body></w:document>'),
    )
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: RealBashTransport())
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    out = plug.route_or_passthrough(
        tool_name="read_file", args={"path": str(f)},
        next_call=_no_next(), task_id="t-desk", tool_call_id="r1",
    )
    parsed = json.loads(out)
    assert parsed.get("extracted_document") is True, parsed
    assert "Quarterly report body" in parsed["content"]
    # Never the raw UTF-8-decoded-ZIP mojibake shape (_relay_file's "data" key).
    assert "data" not in parsed


def test_read_file_docx_honors_offset_and_limit(monkeypatch, tmp_path):
    f = tmp_path / "multi.docx"
    paragraphs = "".join(f"<w:p><w:r><w:t>line{i}</w:t></w:r></w:p>" for i in range(1, 6))
    _write_docx(
        str(f),
        f'<?xml version="1.0"?><w:document xmlns:w="{_NS_W}"><w:body>{paragraphs}</w:body></w:document>',
    )
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: RealBashTransport())
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    out = plug.route_or_passthrough(
        tool_name="read_file", args={"path": str(f), "offset": 2, "limit": 2},
        next_call=_no_next(), task_id="t-desk", tool_call_id="r2",
    )
    parsed = json.loads(out)
    assert "line2" in parsed["content"]
    assert "line3" in parsed["content"]
    assert "line1" not in parsed["content"]
    assert "line4" not in parsed["content"]
    assert parsed["truncated"] is True


def test_read_file_corrupt_docx_over_relay_surfaces_actionable_error(monkeypatch, tmp_path):
    f = tmp_path / "bad.docx"
    f.write_bytes(b"not a zip")
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: RealBashTransport())
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    out = plug.route_or_passthrough(
        tool_name="read_file", args={"path": str(f)},
        next_call=_no_next(), task_id="t-desk", tool_call_id="r3",
    )
    parsed = json.loads(out)
    assert "error" in parsed
    assert "extraction failed" in parsed["error"].lower()
    assert "docx" in parsed["error"].lower()


def test_read_file_plain_text_still_uses_the_raw_frame_path(monkeypatch, tmp_path):
    # Non-extractable extensions must keep behaving exactly as they do
    # today — the raw `read` frame, no line numbers, no pagination.
    ft = RawFrameTransport(data="plain text content\n")
    monkeypatch.setattr(plug, "_make_transport", lambda url, ident: ft)
    monkeypatch.setattr(plug, "_safety_block", lambda *a, **k: None)

    out = plug.route_or_passthrough(
        tool_name="read_file", args={"path": str(tmp_path / "a.txt")},
        next_call=_no_next(), task_id="t-desk", tool_call_id="r4",
    )
    parsed = json.loads(out)
    assert parsed["data"] == "plain text content\n"
    assert "extracted_document" not in parsed
    read_frames = [f for f in ft.sent if f["type"] == "read"]
    assert len(read_frames) == 1
