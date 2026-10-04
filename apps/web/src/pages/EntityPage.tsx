import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import type { EntityCandidate, EntityContextResponse, GraphTemplateResult } from "../api/types";
import { Badge, DataTable, Empty, ErrorNotice, KeyValues, Panel, shortUri } from "../components/ui";
import { useSession } from "../state/session";

export function EntityPage() {
  const { entityId } = useParams();
  const navigate = useNavigate();
  const { request } = useSession();
  const [query, setQuery] = useState("Acme Corpp");
  const [candidates, setCandidates] = useState<EntityCandidate[] | null>(null);
  const [detail, setDetail] = useState<EntityContextResponse | null>(null);
  const [provenance, setProvenance] = useState<Array<Record<string, string>>>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  async function resolve(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const result = await request<{ candidates: EntityCandidate[] }>("/entities/resolve", {
        method: "POST",
        body: { query, limit: 10 },
      });
      setCandidates(result.candidates);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : "Resolution failed");
    }
  }

  useEffect(() => {
    if (!entityId) return;
    let cancelled = false;
    setError(null);
    Promise.all([
      request<EntityContextResponse>(`/entities/${entityId}`),
      request<GraphTemplateResult>("/graph/templates/entity_provenance", {
        method: "POST",
        body: { parameters: { entity_id: entityId } },
      }).catch(() => null),
      request<GraphTemplateResult>("/graph/templates/entity_types", {
        method: "POST",
        body: { parameters: { entity_id: entityId } },
      }).catch(() => null),
    ])
      .then(([context, graphProvenance, graphTypes]) => {
        if (cancelled) return;
        setDetail(context);
        setProvenance(graphProvenance?.rows ?? []);
        setTypes((graphTypes?.rows ?? []).map((row) => shortUri(row.type)));
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof ApiError ? caught.detail : "Entity not available");
      });
    return () => {
      cancelled = true;
    };
  }, [entityId, request]);

  return (
    <div className="page">
      <section className="hero compact">
        <p className="eyebrow mono">Entity resolution</p>
        <h1>Entity explorer</h1>
        <p className="intro">
          Names are normalized, matched against every observed alias (trigram candidates, RapidFuzz
          scoring) and returned with a confidence band. Unconfirmed matches stay in review.
        </p>
      </section>
      <form className="ask compact" onSubmit={resolve}>
        <label htmlFor="entity-query">Name or identifier</label>
        <input id="entity-query" value={query} onChange={(event) => setQuery(event.target.value)} />
        <button className="button" type="submit">
          Resolve
        </button>
      </form>
      <ErrorNotice error={error} />
      <div className="grid">
        {candidates && (
          <Panel className="span-2" title="Candidates" kicker="authorized suppliers only">
            {candidates.length ? (
              <ul className="plain">
                {candidates.map((candidate) => (
                  <li key={candidate.canonical_entity_id}>
                    <button
                      type="button"
                      className="link-button"
                      onClick={() => navigate(`/entities/${candidate.canonical_entity_id}`)}
                    >
                      {candidate.preferred_name}
                    </button>{" "}
                    <Badge value={candidate.match_band ?? "MATCH"} label={candidate.match_band ?? undefined} />{" "}
                    <span className="small muted">
                      {((candidate.confidence_score ?? 0) * 100).toFixed(0)}% via {candidate.match_method} on "
                      {candidate.matched_alias}" ({candidate.source_system})
                      {candidate.review_required ? " · review required" : ""}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No authorized supplier matched.</Empty>
            )}
          </Panel>
        )}
        {detail && (
          <>
            <Panel
              title={detail.entity.preferred_name}
              kicker={detail.entity.canonical_entity_id}
              actions={<Badge value={detail.entity.status} />}
            >
              <KeyValues
                items={[
                  ["Risk rating", detail.entity.risk_rating],
                  ["Classes (with inference)", types.join(", ") || "-"],
                  ["Requisitions in scope", String(detail.related_requisition_ids.length)],
                  ["Purchase orders in scope", String(detail.related_purchase_order_ids.length)],
                  ["Active contracts", detail.active_contract_ids.join(", ") || "none"],
                ]}
              />
              <p className="small">
                <Link to={`/graph?focus=${detail.entity.canonical_entity_id}`}>Explore in graph</Link>
              </p>
            </Panel>
            <Panel title="Aliases and match evidence" kicker="never discarded">
              <DataTable
                rows={detail.aliases.map((alias) => ({
                  alias: alias.alias,
                  source_system: alias.source_system,
                  source_record: alias.source_record_id,
                  method: alias.resolution_method,
                  confidence: alias.confidence_score,
                  review: alias.review_required ? "yes" : "no",
                }))}
              />
            </Panel>
            <Panel className="span-2" title="Provenance" kicker="PROV-O assertions in the context graph">
              <DataTable rows={provenance} emptyText="Graph provenance unavailable." />
            </Panel>
          </>
        )}
      </div>
    </div>
  );
}
