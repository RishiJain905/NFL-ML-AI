"""Write the Ask the Engine mockup's payloads: the real app's answers, in-process (AE01).

Runs the control room (`create_app` + `TestClient`, no server, no browser) on the real data
root with the real Ask engine and model, and saves every answer the page needs, so the mockup
shows only real numbers and its JavaScript reads the same shapes as the React page
(`AskResponse`, `AskExamplesResponse` in web/src/api/types.ts):

- the shell (`meta`, `weeks`, `team_info`) and `examples` (`GET /api/ask/examples`), also as
  they read with Neo4j down (`examples_graph_down`);
- `answers.<key>`: one real `POST /api/ask` per card state: a graph template, a SQL template,
  a free-form query, a refusal by code and by the router, a "which one?" and its follow-up,
  no rows, the graph down (a stand-in status says so) and the model down (the LLM client
  answers 503 from a mock transport: nothing leaves the machine for that one);
- `errors.<key>`: the request-level refusals (409 busy, 429 rate limited, 422 too long).

About ten real questions: ~$0.01 of OpenRouter credit at DeepSeek V4.1 Flash 1017 "max".
Writes `{NFL_DATA_ROOT}/cache/ask-mockup/payloads.json` only; no data goes into git.

    $env:PYTHONIOENCODING = "utf-8"
    uv run python documentation/ask-the-engine/mockup/dump_payloads.py [--only key,key]
"""

from __future__ import annotations

import argparse
import json
import time

import httpx
from fastapi.testclient import TestClient

from nflengine.app.ask_service import AskService
from nflengine.app.server import AppSettings, create_app
from nflengine.app.status import ServiceStatus
from nflengine.paths import ensure_data_root

QUESTIONS = {
    "template_graph": ("Who has Patrick Mahomes thrown to the most this season?", {}),
    "template_sql": ("What's the Bills' record against the Chiefs since 2018?", {}),
    "freeform": ("Which kickers made the most field goals of 50 or more yards in 2024?", {}),
    "lists": ("Who has worked under Kyle Shanahan?", {}),
    "refused_code": ("Delete all the games from 2019", {}),
    "refused_router": ("What will the weather be in Green Bay on Sunday?", {}),
    "clarify": ("How many catches did Michael Thomas have in 2019?", {}),
    "clarify_picked": ("How many catches did Michael Thomas have in 2019?", {"pick": True}),
    "empty": ("Who are the former teammates on this week's Bills-Chiefs game?", {}),
}
BASE = "http://127.0.0.1:8765"


class FixedStatus(ServiceStatus):
    """A Neo4j status that says what it's told (the graph-down card)."""

    def __init__(self, state: str) -> None:
        super().__init__(ping=lambda: (state, "stand-in for the mockup"), every_s=3600)
        self.check_now()

    def start(self) -> None:
        pass


def client_for(token: str, **extra) -> TestClient:
    settings = AppSettings(token=token, **extra)
    return TestClient(create_app(settings), base_url=BASE)


def post(client: TestClient, token: str, body: dict) -> tuple[int, dict]:
    r = client.post("/api/ask", json=body, headers={"origin": BASE, "x-cr-token": token})
    return r.status_code, r.json()


def failing_llm_factory(paths):
    """The real engine whose model never answers (HTTP 503 from a mock transport)."""
    from nflengine.ask.config import ask_config
    from nflengine.ask.llm import AskLLM
    from nflengine.ask.route import AskEngine

    cfg = ask_config()
    dead = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503)))
    return AskEngine.build(AskLLM(cfg.llm, client=dead), cfg, paths)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated answer keys to (re)ask")
    args = ap.parse_args()
    only = {k for k in args.only.split(",") if k}

    paths = ensure_data_root(create_dirs=False)
    out_dir = paths.cache / "ask-mockup"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "payloads.json"
    payloads = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else {}
    payloads.setdefault("answers", {})
    payloads.setdefault("errors", {})

    token = "mockup-token"
    real = ServiceStatus()
    real.check_now()
    ask = AskService(background=False)
    c = client_for(token, services=real, ask=ask)

    for key, path in (
        ("meta", "/api/meta"),
        ("weeks", "/api/weeks"),
        ("team_info", "/api/team-info"),
    ):
        payloads[key] = c.get(path).json()
    payloads["examples"] = c.get("/api/ask/examples").json()
    down = client_for(token, services=FixedStatus("down"), ask=ask)
    payloads["examples_graph_down"] = down.get("/api/ask/examples").json()

    for key, (question, opts) in QUESTIONS.items():
        if only and key not in only:
            continue
        body = {"question": question}
        if opts.get("pick"):
            first = payloads["answers"].get("clarify", {}).get("body") or {}
            choice = (first.get("choices") or [{}])[0]
            option = (choice.get("options") or [{}])[0]
            body["picks"] = [
                {
                    "text": choice.get("text"),
                    "kind": choice.get("kind"),
                    "value": option.get("value"),
                }
            ]
        t = time.monotonic()
        status, answer = post(c, token, body)
        payloads["answers"][key] = {"request": body, "http_status": status, "body": answer}
        print(f"{key:16} {status} {answer.get('status')} {answer.get('route_label')} "
              f"{time.monotonic() - t:5.1f}s ${answer.get('cost')}")  # fmt: skip

    if not only or "graph_down" in only:
        status, answer = post(down, token, {"question": "Who has worked under Kyle Shanahan?"})
        payloads["answers"]["graph_down"] = {
            "request": {"question": "Who has worked under Kyle Shanahan?"},
            "http_status": status,
            "body": answer,
            "note": "Neo4j reported down by a stand-in status",
        }
        print("graph_down      ", status, answer.get("status"), answer.get("route_label"))
    if not only or "llm_down" in only:
        dead = client_for(
            token,
            services=real,
            ask=AskService(engine_factory=failing_llm_factory, background=False),
        )
        q = {"question": "Who led the NFL in rushing yards in 2024?"}
        status, answer = post(dead, token, q)
        payloads["answers"]["llm_down"] = {
            "request": q,
            "http_status": status,
            "body": answer,
            "note": "the model's endpoint answered 503 (a mock transport)",
        }
        print(
            "llm_down        ",
            status,
            answer.get("status"),
            (answer.get("error") or {}).get("code"),
        )

    # request-level refusals (no model call)
    with ask._busy:  # a question already running
        status, body = post(c, token, {"question": "Delete everything"})
    payloads["errors"]["busy"] = {"http_status": status, "body": body}
    limited = client_for(token, services=real, ask=AskService(background=False, rate_per_min=1,
                         engine_factory=lambda p: ask.engine(p)))  # fmt: skip
    post(limited, token, {"question": "Delete everything"})
    status, body = post(limited, token, {"question": "Delete everything"})
    payloads["errors"]["rate_limited"] = {"http_status": status, "body": body}
    status, body = post(c, token, {"question": "x" * 501})
    payloads["errors"]["too_long"] = {"http_status": status, "body": body}

    ask.close()
    out_file.write_text(json.dumps(payloads, indent=1, default=str), encoding="utf-8")
    print("wrote", out_file.relative_to(paths.root))


if __name__ == "__main__":
    main()
