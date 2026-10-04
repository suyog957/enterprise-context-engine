import { useState } from "react";

import { ApiError, newIdempotencyKey } from "../api/client";
import type { ApprovalRequestResult, PurchaseOrderExecution } from "../api/types";
import { useSession } from "../state/session";
import { Badge, ErrorNotice, KeyValues } from "./ui";

/**
 * Human-in-the-loop step for purchase orders. The agent only proposes; this panel
 * executes through the protected endpoint with a fresh idempotency key, which the API
 * re-checks against authorization, OPA policy and approval state.
 */
export function ProposalPanel({
  requisitionId,
  status,
  onChanged,
}: {
  requisitionId: string;
  status: string;
  onChanged?: () => void;
}) {
  const { request } = useSession();
  const [approval, setApproval] = useState<ApprovalRequestResult | null>(null);
  const [execution, setExecution] = useState<PurchaseOrderExecution | null>(null);
  const [idempotencyKey] = useState(() => newIdempotencyKey(`ui-${requisitionId}`));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run<T>(action: () => Promise<T>, done: (value: T) => void) {
    setBusy(true);
    setError(null);
    try {
      done(await action());
      onChanged?.();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  const requestApproval = () =>
    run(
      () =>
        request<ApprovalRequestResult>(`/requisitions/${requisitionId}/approval-requests`, {
          method: "POST",
        }),
      setApproval,
    );

  const confirm = () =>
    run(
      () =>
        request<PurchaseOrderExecution>(`/requisitions/${requisitionId}/create-po`, {
          method: "POST",
          body: {},
          headers: { "Idempotency-Key": idempotencyKey },
        }),
      setExecution,
    );

  return (
    <div className="proposal" data-testid="proposal-panel">
      <div className="proposal-head">
        <strong>Proposed action: create purchase order</strong>
        <Badge value={status} />
      </div>
      {status === "CONFIRMATION_REQUIRED" && (
        <>
          <p className="small">
            Policy currently allows this. Confirming executes through the protected endpoint,
            which re-checks policy and uses idempotency key <code>{idempotencyKey}</code>.
          </p>
          <button type="button" className="button" disabled={busy} onClick={confirm}>
            Confirm purchase order
          </button>
        </>
      )}
      {status === "APPROVAL_REQUIRED" && (
        <>
          <p className="small">
            Manager approval is required first. A manager (for example Buyer 10) approves the
            request; then ask the agent again or confirm from the requisition page.
          </p>
          <button type="button" className="button" disabled={busy} onClick={requestApproval}>
            Request manager approval
          </button>
        </>
      )}
      {approval && (
        <KeyValues
          items={[
            ["Approval request", <code key="id">{approval.approval_id}</code>],
            ["Status", <Badge key="status" value={approval.status} />],
            ["Expires", new Date(approval.expires_at).toLocaleString()],
          ]}
        />
      )}
      {execution && (
        <KeyValues
          items={[
            ["Result", <Badge key="status" value={execution.status} />],
            ["Mode", execution.dry_run ? "Dry run - no record written" : "Committed"],
            ["Purchase order", execution.purchase_order_id ?? "-"],
            ["Idempotent replay", execution.idempotent_replay ? "yes" : "no"],
          ]}
        />
      )}
      <ErrorNotice error={error} />
    </div>
  );
}
