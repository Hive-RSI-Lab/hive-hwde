"""mcp_server.py driven over stdio, as an MCP client would (backlog item 8).

Every test starts the server as a subprocess, speaks newline-delimited
JSON-RPC to it and reads hwde's JSON back, against a copy of the frozen
fixture workspace tests/fixtures/mcp_ws (bb-adc board from tests/fixtures/
bb_adc). The read-only contract is checked by hashing the whole workspace
tree before and after a call.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / ".claude" / "skills" / "hwde" / "scripts" / "mcp_server.py"
FIX = ROOT / "tests" / "fixtures"


class Client:
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8")
        self.next_id = 0

    def send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def raw(self, line: str) -> dict:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def request(self, method: str, params: dict | None = None) -> dict:
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method,
                   "params": params or {}})
        reply = json.loads(self.proc.stdout.readline())
        assert reply["id"] == self.next_id
        return reply

    def call(self, name: str, **arguments) -> dict:
        reply = self.request("tools/call", {"name": name,
                                            "arguments": arguments})
        return reply["result"]

    def close(self) -> int:
        self.proc.stdin.close()
        rc = self.proc.wait(timeout=30)
        self.proc.stdout.close()
        self.proc.stderr.close()
        return rc


@pytest.fixture
def client():
    c = Client()
    init = c.request("initialize", {"protocolVersion": "2025-06-18",
                                    "capabilities": {},
                                    "clientInfo": {"name": "test"}})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    c.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    yield c
    assert c.close() == 0


@pytest.fixture
def ws(tmp_path) -> Path:
    w = tmp_path / "bb-adc"
    shutil.copytree(FIX / "mcp_ws", w)
    (w / "kicad").mkdir()
    for f in (FIX / "bb_adc").iterdir():
        shutil.copy2(f, w / "kicad" / f.name)
    return w


def tree_digest(root: Path) -> dict:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def test_handshake_and_tool_table(client):
    # the initialized notification got no reply: the next line is this answer
    assert client.request("ping")["result"] == {}
    tools = client.request("tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "hwde_route", "hwde_state", "hwde_gate", "hwde_dfm_check",
        "hwde_review"}
    for t in tools:
        assert t["inputSchema"]["type"] == "object"


def test_protocol_errors(client):
    assert client.request("resources/list")["error"]["code"] == -32601
    assert client.raw("{not json")["error"]["code"] == -32700
    bad = client.request("tools/call", {"name": "hwde_nope", "arguments": {}})
    assert bad["error"]["code"] == -32602


def test_unsupported_protocol_version_gets_ours():
    c = Client()
    init = c.request("initialize", {"protocolVersion": "1999-01-01"})
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert c.close() == 0


def test_state_views_read_the_workspace(client, ws):
    before = tree_digest(ws)
    res = client.call("hwde_state", workspace=str(ws), view="show")
    assert not res["isError"]
    assert payload(res)["board"] == "bb-adc"
    assert payload(res)["phase"] == "P0"
    res = client.call("hwde_state", workspace=str(ws))  # default view: resume
    assert not res["isError"]
    assert payload(res)["cmd"] == "resume"
    assert tree_digest(ws) == before


def test_state_refuses_a_dir_without_state_json(client, tmp_path):
    res = client.call("hwde_state", workspace=str(tmp_path))
    assert res["isError"]
    assert "no state.json" in res["content"][0]["text"]


def test_route_plans_and_lists(client, ws):
    res = client.call("hwde_route", task="is this board manufacturable?",
                      workspace=str(ws))
    assert payload(res)["match"]["verb"] == "dfm-check"
    listed = payload(client.call("hwde_route", list=True))
    assert "review" in {v["verb"] for v in listed["verbs"]}


def test_gate_without_a_name_lists_gates(client):
    res = client.call("hwde_gate")
    assert not res["isError"]
    assert {"erc", "drc_routed", "verify", "dfm"} <= set(payload(res)["gates"])


def test_commit_needs_write(client, ws):
    before = tree_digest(ws)
    for tool, extra in (("hwde_gate", {"gate": "place"}),
                        ("hwde_dfm_check", {})):
        res = client.call(tool, workspace=str(ws), commit="gate pass",
                          **extra)
        assert res["isError"]
        assert "needs write=true" in res["content"][0]["text"]
    assert tree_digest(ws) == before


def test_missing_input_is_a_skip_not_an_error(client, ws):
    # the fixture has no schematic, so erc has nothing to judge
    res = client.call("hwde_gate", workspace=str(ws), gate="erc")
    assert not res["isError"]
    assert payload(res)["status"] == "skipped"


def test_gate_is_read_only_by_default(client, ws):
    # place runs without kicad-cli, so this holds on any host
    before = tree_digest(ws)
    res = client.call("hwde_gate", workspace=str(ws), gate="place")
    assert not res["isError"], res["content"][0]["text"]
    body = payload(res)
    assert body["gate"] == "place" and body["status"] in ("pass", "fail")
    assert "record_result" not in body
    assert tree_digest(ws) == before


def test_write_records_the_gate_in_state(client, ws):
    res = client.call("hwde_gate", workspace=str(ws), gate="place",
                      write=True)
    assert not res["isError"], res["content"][0]["text"]
    assert payload(res)["record_result"]["ok"]
    state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
    assert "place" in state["gates"]


def test_write_must_be_a_boolean(client, ws):
    res = client.call("hwde_gate", workspace=str(ws), gate="place",
                      write="yes")
    assert res["isError"]


def _kicad_cli():
    sys.path.insert(0, str(SERVER.parent / "lib"))
    import env
    return env.find_kicad_cli()


needs_kicad = pytest.mark.skipif(not _kicad_cli(),
                                 reason="kicad-cli not found (env.py)")


@needs_kicad
def test_dfm_check_is_read_only(client, ws):
    before = tree_digest(ws)
    res = client.call("hwde_dfm_check", workspace=str(ws))
    assert not res["isError"], res["content"][0]["text"]
    assert payload(res)["gate"] == "dfm"
    # kicad-cli's .kicad_prl side file is cleaned up too
    assert tree_digest(ws) == before


@needs_kicad
def test_review_runs_every_gate_read_only(client, ws):
    before = tree_digest(ws)
    res = client.call("hwde_review", workspace=str(ws))
    body = payload(res)
    assert set(body["gates"]) == {"erc", "drc_routed", "verify", "dfm"}
    assert body["gates"]["erc"] == "skipped"
    assert body["recorded"] is False
    assert body["state"]["cmd"] == "resume"
    assert tree_digest(ws) == before
