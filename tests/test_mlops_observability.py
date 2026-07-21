from __future__ import annotations

from velmo.mlops.observability import (
    InstrumentedClassifier,
    InstrumentedExtractor,
    InstrumentedJudge,
    InstrumentedLLM,
    NullSink,
    estimate_cost,
)


def test_null_sink_does_nothing_and_returns_no_url() -> None:
    sink = NullSink()
    sink.on_llm_call("agent", 100, 50.0, 0.001)  # ne doit pas lever
    assert sink.run_url("run-1") is None


def test_estimate_cost_uses_configured_pricing(monkeypatch) -> None:
    monkeypatch.setenv(
        "TOKEN_PRICING", '{"gpt-5-mini": 0.002, "Mistral-Large-3": 0.004}'
    )
    cost = estimate_cost(tokens=1000, model="gpt-5-mini")
    assert cost == 0.002


def test_estimate_cost_unknown_model_returns_zero_not_error() -> None:
    cost = estimate_cost(tokens=1000, model="modele-inconnu")
    assert cost == 0.0


def test_mask_sensitive_data_redacts_pii_and_secrets() -> None:
    from velmo.mlops.observability import mask_sensitive_data

    assert "4242" not in mask_sensitive_data(data="ma carte est 4242 4242 4242 4242")
    assert "sk-" not in mask_sensitive_data(data="voici sk-abcdef1234567890abcdef1234567890")


def test_mask_sensitive_data_recurses_into_dict_and_list() -> None:
    from velmo.mlops.observability import mask_sensitive_data

    masked = mask_sensitive_data(
        data={"messages": ["carte 4242 4242 4242 4242", {"nested": "ok"}]}
    )
    assert "4242" not in masked["messages"][0]
    assert masked["messages"][1]["nested"] == "ok"


def test_mask_sensitive_data_passthrough_for_non_strings() -> None:
    from velmo.mlops.observability import mask_sensitive_data

    assert mask_sensitive_data(data=42) == 42
    assert mask_sensitive_data(data=None) is None


class _RecordingSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float, float, str | None, str | None, str | None]] = []

    def on_llm_call(
        self,
        component: str,
        tokens: int,
        latency_ms: float,
        cost: float,
        *,
        input: str | None = None,
        output: str | None = None,
        model: str | None = None,
    ) -> None:
        self.calls.append((component, tokens, latency_ms, cost, input, output, model))

    def run_url(self, run_id: str) -> str | None:
        return None


class _FakeLLM:
    def invoke(self, system: str, context: str, message: str) -> str:
        return "reponse"


class _FakeExtractor:
    def extract(self, user_message: str, assistant_message: str):  # type: ignore[no-untyped-def]
        from velmo.memory.extractor import ExtractionResult

        return ExtractionResult()


class _FakeClassifier:
    def score(self, text: str) -> dict[str, float]:
        return {"hate": 0.0}

    def score_detailed(self, text: str):  # type: ignore[no-untyped-def]
        from velmo.guardrails.classifier import ClassifierResult

        return ClassifierResult(scores={"hate": 0.0}, reasoning={})


class _FakeJudge:
    def evaluate(self, text: str, agent_response: str | None = None) -> dict[str, float | str]:
        return {"manipulation": 0.0, "secret_interne": 0.0, "hors_role": 0.0, "reasoning": "ok"}


def test_instrumented_llm_forwards_result_and_emits_one_call() -> None:
    sink = _RecordingSink()
    llm = InstrumentedLLM(_FakeLLM(), sink, "agent", "gpt-5-mini")
    result = llm.invoke("system", "context", "message")
    assert result == "reponse"
    assert len(sink.calls) == 1
    component, tokens, latency_ms, cost, input_, output, model = sink.calls[0]
    assert component == "agent"
    assert tokens > 0
    assert latency_ms >= 0.0
    assert cost >= 0.0
    assert input_ == "message"
    assert output == "reponse"
    assert model == "gpt-5-mini"


def test_instrumented_extractor_forwards_result_and_emits_one_call() -> None:
    sink = _RecordingSink()
    extractor = InstrumentedExtractor(_FakeExtractor(), sink, "memory_extractor", "gpt-5-mini")
    extractor.extract("bonjour", "bonjour a vous")
    assert len(sink.calls) == 1
    component, tokens, latency_ms, cost, *_ = sink.calls[0]
    assert component == "memory_extractor"


def test_instrumented_classifier_emits_one_call_per_score_call() -> None:
    sink = _RecordingSink()
    classifier = InstrumentedClassifier(_FakeClassifier(), sink, "guardrails_classifier")
    classifier.score("texte")
    classifier.score_detailed("texte")
    assert len(sink.calls) == 2
    assert all(c[0] == "guardrails_classifier" for c in sink.calls)


def test_instrumented_judge_forwards_result_and_emits_one_call() -> None:
    sink = _RecordingSink()
    judge = InstrumentedJudge(_FakeJudge(), sink, "guardrails_judge", "gpt-5-mini")
    verdict = judge.evaluate("texte", "reponse agent")
    assert verdict["manipulation"] == 0.0
    assert len(sink.calls) == 1
    component, tokens, latency_ms, cost, *_ = sink.calls[0]
    assert component == "guardrails_judge"


def test_cost_accumulating_sink_sums_costs_and_forwards_to_inner() -> None:
    from velmo.mlops.observability import CostAccumulatingSink

    inner = _RecordingSink()
    acc = CostAccumulatingSink(inner)
    acc.on_llm_call("agent", 100, 10.0, 0.01)
    acc.on_llm_call("memory_extractor", 50, 5.0, 0.02)
    assert acc.total_cost == 0.03
    assert len(inner.calls) == 2  # relayé au sink réel (Langfuse/NullSink)


def test_get_sink_falls_back_to_null_sink_without_langfuse_config(monkeypatch) -> None:
    """Comme `get_llm`/`get_classifier`/`get_judge`/`get_quality_scorer` : sans
    config, repli déterministe, hors-ligne — jamais d'appel réseau réel en
    test (Global Constraints)."""
    from velmo.mlops.observability import NullSink, get_sink

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    assert isinstance(get_sink(), NullSink)


def test_langfuse_sink_reuses_injected_client(monkeypatch) -> None:
    """Un client injecté ne doit jamais être reconstruit — sinon chaque tour
    de conversation ouvrirait sa propre connexion Langfuse au lieu de
    partager celle du process long-vécu (API live)."""
    from velmo.mlops.observability import LangfuseSink

    class _FakeObservation:
        id = "obs-1"

        def update(self, **_: object) -> None:
            pass

        def end(self) -> None:
            pass

    class _FakeClient:
        def __init__(self) -> None:
            self.start_calls: list[dict[str, object]] = []

        def create_trace_id(self) -> str:
            return "generated-trace-id"

        def start_observation(self, **kwargs: object) -> _FakeObservation:
            self.start_calls.append(kwargs)
            return _FakeObservation()

    fake_client = _FakeClient()
    sink = LangfuseSink(client=fake_client, trace_id="turn-123", parent_span_id="root-1")  # type: ignore[arg-type]
    sink.on_llm_call("agent", 10, 5.0, 0.0, input="hi", output="hello", model="m")

    assert len(fake_client.start_calls) == 1
    call = fake_client.start_calls[0]
    assert call["trace_context"] == {"trace_id": "turn-123", "parent_span_id": "root-1"}


def test_get_langfuse_client_falls_back_to_none_without_config(monkeypatch) -> None:
    from velmo.mlops.observability import get_langfuse_client

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_BASE_URL", raising=False)
    get_langfuse_client.cache_clear()
    assert get_langfuse_client() is None


def test_instrumented_llm_resolves_sink_from_context_when_none_given() -> None:
    from velmo.mlops.observability import reset_current_sink, set_current_sink

    sink = _RecordingSink()
    token = set_current_sink(sink)
    try:
        llm = InstrumentedLLM(_FakeLLM(), None, "agent", "gpt-5-mini")
        llm.invoke("system", "context", "message")
    finally:
        reset_current_sink(token)
    assert len(sink.calls) == 1


def test_instrumented_llm_explicit_sink_wins_over_context() -> None:
    from velmo.mlops.observability import reset_current_sink, set_current_sink

    context_sink = _RecordingSink()
    explicit_sink = _RecordingSink()
    token = set_current_sink(context_sink)
    try:
        llm = InstrumentedLLM(_FakeLLM(), explicit_sink, "agent", "gpt-5-mini")
        llm.invoke("system", "context", "message")
    finally:
        reset_current_sink(token)
    assert len(explicit_sink.calls) == 1
    assert len(context_sink.calls) == 0


def test_instrumented_llm_defaults_to_null_sink_outside_any_context() -> None:
    llm = InstrumentedLLM(_FakeLLM(), None, "agent", "gpt-5-mini")
    assert llm.invoke("system", "context", "message") == "reponse"  # ne doit pas lever


def test_traced_respond_passthrough_without_langfuse_configured(monkeypatch) -> None:
    """Sans Langfuse configuré, `traced_respond` doit se comporter exactement
    comme `agent.respond_traced` — aucun effet de bord, aucune exception."""
    from velmo.mlops import observability as obs

    monkeypatch.setattr(obs, "get_langfuse_client", lambda: None)

    class _FakeAgent:
        def respond_traced(self, user_id: str, message: str):
            yield "final", {"answer": "ok", "status": "ok", "latency_ms": 1}

    events = list(obs.traced_respond(_FakeAgent(), "u1", "salut"))  # type: ignore[arg-type]
    assert events == [("final", {"answer": "ok", "status": "ok", "latency_ms": 1})]


def test_traced_respond_emits_root_and_stage_spans(monkeypatch) -> None:
    from velmo.mlops import observability as obs

    class _FakeObservation:
        def __init__(self, obs_id: str) -> None:
            self.id = obs_id
            self.updates: list[dict[str, object]] = []
            self.ended = False

        def update(self, **kwargs: object) -> None:
            self.updates.append(kwargs)

        def end(self) -> None:
            self.ended = True

    class _FakeClient:
        def __init__(self) -> None:
            self.started: list[dict[str, object]] = []

        def create_trace_id(self) -> str:
            return "trace-xyz"

        def start_observation(self, **kwargs: object) -> _FakeObservation:
            self.started.append(kwargs)
            return _FakeObservation(f"obs-{len(self.started)}")

    fake_client = _FakeClient()
    monkeypatch.setattr(obs, "get_langfuse_client", lambda: fake_client)

    class _FakeAgent:
        def respond_traced(self, user_id: str, message: str):
            yield "input_guardrail", {"allowed": True}
            yield "final", {"answer": "reponse", "status": "ok", "latency_ms": 3}

    events = list(obs.traced_respond(_FakeAgent(), "u1", "salut"))  # type: ignore[arg-type]
    assert [e[0] for e in events] == ["input_guardrail", "final"]

    names = [call["name"] for call in fake_client.started]
    assert names[0] == "chat-turn"
    assert "input_guardrail" in names or "input-guardrail" in names
    root = fake_client.started[0]
    assert root["as_type"] == "span"
    assert root["input"] == {"message": "salut"}
