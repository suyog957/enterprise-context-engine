import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import type { ActionSimulation, AllowedActionsResponse, RequisitionContext } from "../api/types";
import { ProposalPanel } from "../components/ProposalPanel";
import { ActionTable, Badge, ErrorNotice, KeyValues, Panel, Spinner } from "../components/ui";
import { useSession } from "../state/session";

export function RequisitionPage() {
  const { requisitionId = "PR-1007" } = useParams();
  const navigate = useNavigate();
  const { request, principalId } = useSession();
  const [input, setInput] = useState(requisitionId);
  const [context, setContext] = useState<RequisitionContext | null>(null);
  const [actions, setActions] = useState<AllowedActionsResponse | null>(null);
  const [simulation, setSimulation] = useState<ActionSimulation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setSimulation(null);
    try {
      const [details, allowed] = await Promise.all([
        request<RequisitionContext>(`/requisitions/${requisitionId}`),
        request<AllowedActionsResponse>(`/requisitions/${requisitionId}/allowed-actions`),
      ]);
      setContext(details);
      setActions(allowed);
    } catch (caught) {
      setContext(null);
      setActions(null);
      setError(caught instanceof ApiError ? caught.detail : "Could not load the requisition");
    } finally {
      setLoading(false);
    }
  }, [request, requisitionId]);

  useEffect(() => {
    setInput(requisitionId);
    void load();
  }, [load, requisitionId, principalId]);

  async function simulate() {
    try {
      setSimulation(
        await request<ActionSimulation>(`/requisitions/${requisitionId}/simulate-po`, { method: "POST" }),
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : "Simulation failed");
    }
  }

  function open(event: FormEvent) {
    event.preventDefault();
    navigate(`/requisitions/${input.trim().toUpperCase()}`);
  }

  const create = actions?.actions.find((action) => action.action === "CREATE_PURCHASE_ORDER");
  const proposalStatus =
    create?.status === "AVAILABLE"
      ? "CONFIRMATION_REQUIRED"
      : create?.status === "APPROVAL_REQUIRED"
        ? "APPROVAL_REQUIRED"
        : null;

  return (
    <div className="page">
      <form className="ask compact" onSubmit={open}>
        <label htmlFor="requisition">Requisition</label>
        <input id="requisition" value={input} onChange={(event) => setInput(event.target.value)} />
        <button className="button" type="submit">
          Open
        </button>
      </form>
      {loading && <Spinner label="Loading requisition" />}
      <ErrorNotice error={error} />
      {context && actions && (
        <div className="grid">
          <Panel
            title={context.requisition_id}
            kicker="authoritative facts (PostgreSQL)"
            actions={<Badge value={context.state} />}
          >
            <KeyValues
              items={[
                ["Amount", `${context.currency} ${Number(context.amount).toLocaleString(undefined, { minimumFractionDigits: 2 })}`],
                ["Buyer", `${context.buyer_name} (${context.buyer_id})`],
                ["Business unit", context.business_unit_id],
                ["Categories", context.categories.join(", ") || "-"],
                [
                  "Supplier",
                  context.supplier.canonical_entity_id ? (
                    <Link key="s" to={`/entities/${context.supplier.canonical_entity_id}`}>
                      {context.supplier.preferred_name}
                    </Link>
                  ) : (
                    "unresolved"
                  ),
                ],
                ["Supplier status", <Badge key="st" value={context.supplier.status} />],
                ["Supplier risk", context.supplier.risk_rating],
                ["Active contracts", context.active_contract_ids.join(", ") || "none"],
                ["Row version", String(context.row_version)],
              ]}
            />
            <p className="small">
              <Link to={`/graph?focus=${context.requisition_id}`}>Explore in graph</Link>
            </p>
          </Panel>
          <Panel
            title="Allowed actions"
            kicker={`OPA policy ${actions.policy_version}`}
            actions={
              <button type="button" className="button ghost" onClick={() => void simulate()}>
                Simulate PO
              </button>
            }
          >
            <ActionTable actions={actions.actions} />
            {simulation && (
              <p className="notice small" data-testid="simulation">
                Simulation: {simulation.available ? "allowed" : "not allowed"}
                {simulation.decision.reason_codes.length
                  ? ` (${simulation.decision.reason_codes.join(", ")})`
                  : ""}{" "}
                · dry run {String(simulation.dry_run)}
              </p>
            )}
          </Panel>
          {proposalStatus && (
            <Panel className="span-2" title="Purchase order" kicker="human confirmation">
              <ProposalPanel requisitionId={context.requisition_id} status={proposalStatus} onChanged={() => void load()} />
            </Panel>
          )}
        </div>
      )}
    </div>
  );
}
