import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import type { EntityContextResponse, GraphTemplateResult } from "../api/types";
import { GraphCanvas, legend } from "../components/GraphCanvas";
import type { GraphEdge, GraphNode } from "../components/GraphCanvas";
import { DataTable, Empty, ErrorNotice, Panel, shortUri } from "../components/ui";
import { useSession } from "../state/session";

const RESOURCE = "https://example.org/enterprise-context/";
const EXPANDABLE = /^(supplier-|PR-|PO-|CON-|BUY-|PROD-|BU-)/;

function canonicalId(uri: string): string {
  return decodeURIComponent(uri.split("/").pop() ?? uri);
}

function kindOf(uri: string, typeUri?: string): string {
  if (typeUri) return shortUri(typeUri);
  if (uri.includes("/supplier/")) return "Supplier";
  if (uri.includes("/requisition/")) return "PurchaseRequisition";
  if (uri.includes("/purchase-order/")) return "PurchaseOrder";
  if (uri.includes("/contract/")) return "Contract";
  if (uri.includes("/buyer/")) return "Buyer";
  if (uri.includes("/product/")) return "Product";
  if (uri.includes("/business-unit/")) return "BusinessUnit";
  return "State";
}

interface NodeDetail {
  properties: Array<Record<string, string>>;
  relationships: Array<Record<string, string>>;
}

export function GraphPage() {
  const { request } = useSession();
  const [params, setParams] = useSearchParams();
  const focus = params.get("focus") ?? "PR-1007";
  const [input, setInput] = useState(focus);
  const [nodes, setNodes] = useState<Map<string, GraphNode>>(new Map());
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [details, setDetails] = useState<Map<string, NodeDetail>>(new Map());
  const [selected, setSelected] = useState<string | null>(null);
  const [extra, setExtra] = useState<{ provenance: Array<Record<string, string>>; aliases: string[] }>({
    provenance: [],
    aliases: [],
  });
  const [error, setError] = useState<string | null>(null);

  const expand = useCallback(
    async (identifier: string, reset: boolean) => {
      setError(null);
      try {
        const result = await request<GraphTemplateResult>("/graph/templates/neighborhood", {
          method: "POST",
          body: { parameters: { entity_id: identifier } },
        });
        const focusUri = Object.values(result.rows[0] ?? {}).length
          ? `${RESOURCE}${kindPath(identifier)}/${encodeURIComponent(identifier)}`
          : null;
        if (!focusUri) {
          setError(`${identifier} has no visible relationships (not found or outside your scope).`);
          return;
        }
        setNodes((previous) => {
          const next = reset ? new Map<string, GraphNode>() : new Map(previous);
          next.set(focusUri, { id: focusUri, label: identifier, kind: kindOf(focusUri) });
          for (const row of result.rows) {
            if (row.neighbor?.startsWith("http")) {
              next.set(row.neighbor, {
                id: row.neighbor,
                label: row.neighborLabel ?? shortUri(row.neighbor),
                kind: kindOf(row.neighbor, row.neighborType),
              });
            }
          }
          return next;
        });
        setEdges((previous) => {
          const base = reset ? [] : previous;
          const added = result.rows
            .filter((row) => row.neighbor?.startsWith("http"))
            .map((row) =>
              row.direction === "out"
                ? { source: focusUri, target: row.neighbor, label: shortUri(row.predicate) }
                : { source: row.neighbor, target: focusUri, label: shortUri(row.predicate) },
            );
          const seen = new Set(base.map((edge) => `${edge.source}|${edge.label}|${edge.target}`));
          return [...base, ...added.filter((edge) => !seen.has(`${edge.source}|${edge.label}|${edge.target}`))];
        });
        setDetails((previous) => {
          const next = reset ? new Map<string, NodeDetail>() : new Map(previous);
          next.set(focusUri, {
            properties: result.rows
              .filter((row) => row.direction === "out" && !row.neighbor?.startsWith("http"))
              .map((row) => ({ property: shortUri(row.predicate), value: row.neighbor })),
            relationships: result.rows
              .filter((row) => row.neighbor?.startsWith("http"))
              .map((row) => ({
                direction: row.direction,
                relationship: shortUri(row.predicate),
                node: row.neighborLabel ?? shortUri(row.neighbor),
              })),
          });
          return next;
        });
        setSelected(focusUri);
      } catch (caught) {
        setError(caught instanceof ApiError ? caught.detail : "Graph query failed");
      }
    },
    [request],
  );

  useEffect(() => {
    setInput(focus);
    void expand(focus, true);
  }, [focus, expand]);

  useEffect(() => {
    if (!selected?.startsWith(RESOURCE)) return;
    const identifier = canonicalId(selected);
    let cancelled = false;
    Promise.all([
      request<GraphTemplateResult>("/graph/templates/entity_provenance", {
        method: "POST",
        body: { parameters: { entity_id: identifier } },
      }).catch(() => null),
      identifier.startsWith("supplier-")
        ? request<EntityContextResponse>(`/entities/${identifier}`).catch(() => null)
        : Promise.resolve(null),
    ]).then(([provenance, entity]) => {
      if (cancelled) return;
      setExtra({
        provenance: provenance?.rows ?? [],
        aliases: entity?.aliases.map((alias) => `${alias.alias} (${alias.source_system})`) ?? [],
      });
    });
    return () => {
      cancelled = true;
    };
  }, [selected, request]);

  function open(event: FormEvent) {
    event.preventDefault();
    setParams({ focus: input.trim() });
  }

  function onSelect(id: string) {
    setSelected(id);
    const identifier = canonicalId(id);
    if (id.startsWith(RESOURCE) && EXPANDABLE.test(identifier) && !details.has(id)) {
      void expand(identifier, false);
    }
  }

  const nodeList = useMemo(() => [...nodes.values()], [nodes]);
  const detail = selected ? details.get(selected) : undefined;

  return (
    <div className="page">
      <section className="hero compact">
        <p className="eyebrow mono">RDF context graph</p>
        <h1>Context graph explorer</h1>
        <p className="intro">
          Click a node to expand it. Requisitions, purchase orders and buyers outside your business
          units are filtered inside the SPARQL query, not hidden afterwards.
        </p>
      </section>
      <form className="ask compact" onSubmit={open}>
        <label htmlFor="focus">Start from</label>
        <input id="focus" value={input} onChange={(event) => setInput(event.target.value)} />
        <button className="button" type="submit">
          Load
        </button>
      </form>
      <ErrorNotice error={error} />
      <div className="grid">
        <Panel className="span-2" title="Graph" kicker={`${nodeList.length} nodes · ${edges.length} edges`}>
          <GraphCanvas nodes={nodeList} edges={edges} selected={selected} onSelect={onSelect} />
          <ul className="legend">
            {legend().map(([kind, color]) => (
              <li key={kind}>
                <span style={{ background: color }} /> {kind}
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title={selected ? shortUri(selected) : "Select a node"} kicker="properties and relationships">
          {detail ? (
            <>
              <DataTable rows={detail.properties} emptyText="No literal properties." />
              <h3>Relationships</h3>
              <DataTable rows={detail.relationships} emptyText="No relationships." />
            </>
          ) : (
            <Empty>Select an expandable node to see its properties.</Empty>
          )}
        </Panel>
        <Panel title="Source systems and provenance" kicker="aliases and assertions">
          {extra.aliases.length > 0 && <p className="small">Aliases: {extra.aliases.join("; ")}</p>}
          <DataTable rows={extra.provenance} emptyText="No provenance assertions for this node." />
        </Panel>
      </div>
    </div>
  );
}

function kindPath(identifier: string): string {
  if (identifier.startsWith("supplier-")) return "supplier";
  if (identifier.startsWith("PR-")) return "requisition";
  if (identifier.startsWith("PO-")) return "purchase-order";
  if (identifier.startsWith("CON-")) return "contract";
  if (identifier.startsWith("BUY-")) return "buyer";
  if (identifier.startsWith("PROD-")) return "product";
  return "business-unit";
}
