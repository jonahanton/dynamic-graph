import asyncio

from dynamic_graph.connectors import FakeFetchClient, FakeSearchClient
from dynamic_graph.connectors.observed import ObservedWeb
from dynamic_graph.examples import hormuz_question
from dynamic_graph.observability import EventLog
from tests.fakes import offline_runtime


def test_observed_web_search_and_fetch(tmp_path):
    runtime = offline_runtime(tmp_path, hormuz_question())
    web = ObservedWeb(
        runtime=runtime, brave=FakeSearchClient(provider="brave"), fetch=FakeFetchClient()
    )

    async def go():
        with runtime.observe(kind="node", actor="research-1"):
            result = await web.search(actor="research-1", query="hormuz transits")
            page = await web.fetch(actor="research-1", url=result.hits[0].url)
        return result, page

    result, page = asyncio.run(go())
    runtime.shutdown()

    assert result.provider == "brave" and result.hits
    assert page.text and page.content_hash.startswith("sha256:")

    events = EventLog.read(runtime.paths.events)
    assert any(e.kind == "web_search" for e in events)
    assert any(e.kind == "fetch" for e in events)
    assert runtime.budget.search_calls == 1 and runtime.budget.fetch_calls == 1
