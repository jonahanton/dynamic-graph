from __future__ import annotations

from dynamic_graph.agents import (
    AgentDeps,
    WorkerResult,
    load_prompt,
    new_id,
    node_brief,
    question_header,
)
from dynamic_graph.agents.schemas import ResearchOutput, SearchPlan
from dynamic_graph.connectors import SearchHit
from dynamic_graph.domain.artifacts import Artifact, ResearchSignal
from dynamic_graph.domain.graph import GraphNode

_MAX_FETCH = 3
_MAX_TEXT = 4000


async def run(node: GraphNode, deps: AgentDeps) -> WorkerResult:
    result = WorkerResult()
    actor = node.node_id
    q = deps.question

    plan = await deps.llm.structured(
        prompt_name="research_plan",
        actor=actor,
        response_model=SearchPlan,
        system=load_prompt("research"),
        user=(
            question_header(q)
            + node_brief(node)
            + f"Available search providers: {deps.web.providers or ['none']}\n"
            + "Plan 1-3 searches."
        ),
        max_tokens=600,
    )

    end_date = q.as_of.isoformat()
    hits: list[SearchHit] = []
    for query in plan.queries[:3]:
        if not deps.web.providers:
            break
        provider = None if query.provider == "any" else query.provider
        freshness = None if query.freshness == "none" else query.freshness
        try:
            search_result = await deps.web.search(
                actor=actor,
                query=query.query,
                provider=provider,
                count=5,
                freshness=freshness,
                end_published_date=end_date,
            )
            hits.extend(search_result.hits)
        except Exception as exc:  # noqa: BLE001 - record and continue
            deps.runtime.emit("web_search_error", actor, f"search failed: {exc}")

    # Coverage artefact (records null/coverage even when nothing was found).
    result.artifacts.append(
        Artifact(
            id=new_id("coverage"),
            kind="source_coverage",
            created_by=actor,
            summary=f"{len(hits)} hits across {len(plan.queries)} queries",
            payload={
                "hit_count": len(hits),
                "query_count": len(plan.queries),
                "providers": deps.web.providers,
            },
        )
    )

    # Fetch a few unique pages and build source artefacts.
    url_to_source: dict[str, str] = {}
    fetched_blocks: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        if len(url_to_source) >= _MAX_FETCH:
            break
        if hit.url in seen:
            continue
        seen.add(hit.url)
        if not deps.web.can_fetch:
            break
        try:
            page = await deps.web.fetch(actor=actor, url=hit.url)
        except Exception as exc:  # noqa: BLE001
            deps.runtime.emit("fetch_error", actor, f"fetch failed for {hit.url}: {exc}")
            continue
        source_id = new_id("source")
        url_to_source[hit.url] = source_id
        result.artifacts.append(
            Artifact(
                id=source_id,
                kind="source",
                created_by=actor,
                summary=(page.title or hit.title or hit.url)[:120],
                payload={
                    "url": page.final_url,
                    "title": page.title or hit.title,
                    "published_at": hit.published_at.isoformat() if hit.published_at else None,
                    "snippet": hit.snippet[:300],
                    "text": page.text[:_MAX_TEXT],
                    "content_hash": page.content_hash,
                },
                file_paths=[],
            )
        )
        fetched_blocks.append(f"SOURCE {source_id} ({page.final_url}):\n{page.text[:_MAX_TEXT]}")

    if not fetched_blocks:
        result.signals.append(
            ResearchSignal(
                id=new_id("signal"),
                created_by=actor,
                kind="missing_data",
                description=f"No usable sources fetched for: {node.objective}",
                suggested_node_kind="research",
            )
        )
        return result

    output = await deps.llm.structured(
        prompt_name="research_extract",
        actor=actor,
        response_model=ResearchOutput,
        system=load_prompt("research"),
        user=(
            question_header(q)
            + node_brief(node)
            + "\nFetched sources:\n\n"
            + "\n\n---\n\n".join(fetched_blocks)
        ),
        max_tokens=1400,
    )

    for item in output.evidence:
        source_ids = [url_to_source[item.source_url]] if item.source_url in url_to_source else []
        result.artifacts.append(
            Artifact(
                id=new_id("evidence"),
                kind="evidence",
                created_by=actor,
                summary=item.claim[:140],
                payload={
                    "claim": item.claim,
                    "stance": item.stance,
                    "strength": item.strength,
                    "quote": item.quote,
                    "source_url": item.source_url,
                },
                source_ids=source_ids,
            )
        )

    for sig in output.signals:
        result.signals.append(
            ResearchSignal(
                id=new_id("signal"),
                created_by=actor,
                kind=sig.kind,
                description=sig.description,
                suggested_node_kind=None
                if sig.suggested_node_kind == "none"
                else sig.suggested_node_kind,
            )
        )

    return result
