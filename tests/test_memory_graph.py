"""Le StateGraph LangGraph persiste le fil de messages par thread_id, survit à
une nouvelle instance de checkpointer pointant sur le même fichier/URL."""

from __future__ import annotations

from velmo.memory.graph import build_graph, get_checkpointer


def test_graph_persists_messages_across_checkpointer_instances(tmp_path) -> None:
    db_path = str(tmp_path / "graph_test.db")

    with get_checkpointer(f"sqlite:///{db_path}") as checkpointer:
        graph = build_graph(checkpointer)
        config = {"configurable": {"thread_id": "th-test-1"}}
        graph.invoke({"messages": [{"role": "user", "content": "bonjour"}]}, config)

    with get_checkpointer(f"sqlite:///{db_path}") as checkpointer:
        graph = build_graph(checkpointer)
        config = {"configurable": {"thread_id": "th-test-1"}}
        state = graph.get_state(config)
        contents = [m["content"] for m in state.values["messages"]]
        assert "bonjour" in contents


def test_graph_isolates_threads_by_thread_id(tmp_path) -> None:
    db_path = str(tmp_path / "graph_test2.db")
    with get_checkpointer(f"sqlite:///{db_path}") as checkpointer:
        graph = build_graph(checkpointer)
        graph.invoke(
            {"messages": [{"role": "user", "content": "secret A"}]},
            {"configurable": {"thread_id": "th-a"}},
        )
        graph.invoke(
            {"messages": [{"role": "user", "content": "secret B"}]},
            {"configurable": {"thread_id": "th-b"}},
        )
        state_a = graph.get_state({"configurable": {"thread_id": "th-a"}})
        contents_a = [m["content"] for m in state_a.values["messages"]]
        assert "secret A" in contents_a
        assert "secret B" not in contents_a
