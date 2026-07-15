from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from wqb_agent_lab.platform import WQBClient, validate_simulation_create_contract


class _ContractHandler(BaseHTTPRequestHandler):
    drift = False

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/authentication":
            self._json(200, {"user": "fixture"})
        elif path == "/operators":
            self._json(200, {"items": []} if self.drift else {"results": []})
        elif path == "/users/self/alphas":
            self._json(200, {"count": "1", "items": []} if self.drift else {"count": 0, "results": []})
        else:
            self._json(404, {})

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def test_read_only_contract_probe_detects_shape_drift() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ContractHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = WQBClient(session=requests.Session(), base_url=f"http://127.0.0.1:{server.server_port}")
        _ContractHandler.drift = False
        assert client.contract_probe()["status"] == "ok"
        _ContractHandler.drift = True
        report = client.contract_probe()
        assert report["status"] == "contract_drift"
        assert {issue["code"] for issue in report["issues"]} == {"operators_not_list", "results_not_list", "count_not_integer"}
    finally:
        _ContractHandler.drift = False
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_simulation_create_requires_location_header() -> None:
    issues = validate_simulation_create_contract(201, {"location": "/simulations/renamed"})
    assert [issue.code for issue in issues] == ["location_header_missing"]
