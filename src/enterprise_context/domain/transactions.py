from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from enterprise_context.config import Settings
from enterprise_context.domain.actions import build_create_po_policy_input
from enterprise_context.domain.requisitions import RequisitionContext, SupplierContext
from enterprise_context.domain.write_models import (
    ActionSimulation,
    ApprovalDecisionRequest,
    ApprovalRequestResult,
    PurchaseOrderCommand,
    PurchaseOrderExecution,
)
from enterprise_context.observability.context import current_request_id, current_trace_id
from enterprise_context.policy.client import OPAClient
from enterprise_context.policy.models import PolicyDecision, ProcurementPolicyInput
from enterprise_context.security.principals import PrincipalContext

ApprovalStatus = Literal["PENDING", "APPROVED", "REJECTED", "EXPIRED", "INVALIDATED"]
PurchaseOrderStatus = Literal["SIMULATED", "DENIED", "APPROVAL_REQUIRED", "CREATED"]


class TransactionError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProcurementTransactions:
    def __init__(
        self,
        settings: Settings,
        policy_evaluator: Callable[[ProcurementPolicyInput], PolicyDecision] | None = None,
        catalog_evaluator: (
            Callable[[ProcurementPolicyInput], dict[str, PolicyDecision]] | None
        ) = None,
    ) -> None:
        self._settings = settings
        self._policy_evaluator = policy_evaluator
        self._catalog_evaluator = catalog_evaluator

    def evaluate_action_catalog(
        self, context: RequisitionContext, principal: PrincipalContext
    ) -> dict[str, PolicyDecision]:
        """OPA decisions for every catalog action; CREATE_PURCHASE_ORDER is approval-aware."""
        create_po = self.evaluate_action(context, principal)
        policy_input = build_create_po_policy_input(context, principal)
        if self._catalog_evaluator is not None:
            others = self._catalog_evaluator(policy_input)
        elif self._policy_evaluator is not None:
            others = {}
        else:
            others = OPAClient(self._settings.opa_url).evaluate_actions(policy_input)
        return {**others, "CREATE_PURCHASE_ORDER": create_po}

    def simulate(self, requisition_id: str, principal: PrincipalContext) -> ActionSimulation:
        with psycopg.connect(
            self._settings.database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            context = self._load_context(connection, requisition_id, principal)
        # Same approval-aware evaluation as action discovery, so discovery, simulation
        # and dry-run execution agree once a matching approval has been granted.
        decision = self.evaluate_action(context, principal)
        return ActionSimulation(
            requisition_id=requisition_id,
            decision=decision,
            available=decision.allowed,
            approval_required=decision.approval_required,
        )

    def evaluate_action(
        self, context: RequisitionContext, principal: PrincipalContext
    ) -> PolicyDecision:
        decision = self._evaluate(context, principal)
        if decision.allowed or not decision.approval_required:
            return decision
        with psycopg.connect(
            self._settings.database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            approval: Any = connection.execute(
                """SELECT arguments FROM approval_request
                   WHERE requester_id = %s AND action_type = 'CREATE_PURCHASE_ORDER'
                     AND resource_id = %s AND resource_version = %s
                     AND policy_version = %s AND status = 'APPROVED'
                     AND expires_at > now()
                   ORDER BY decided_at DESC LIMIT 1""",
                (
                    principal.principal_id,
                    context.requisition_id,
                    context.row_version,
                    decision.policy_version,
                ),
            ).fetchone()
        if approval and approval["arguments"] == self._approval_arguments(context):
            return self._evaluate(context, principal, manager_approval_exists=True)
        return decision

    def request_approval(
        self, requisition_id: str, principal: PrincipalContext
    ) -> ApprovalRequestResult:
        if "AUDITOR" in principal.roles:
            raise TransactionError("Auditors cannot request purchase actions", status_code=403)
        with psycopg.connect(
            self._settings.database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            with connection.transaction():
                context = self._load_context(connection, requisition_id, principal, lock=True)
                decision = self._evaluate(context, principal)
                if decision.allowed or not decision.approval_required:
                    raise TransactionError("This action does not currently require approval")
                approval_id = uuid4()
                expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
                arguments = self._approval_arguments(context)
                connection.execute(
                    """INSERT INTO approval_request
                       (approval_id, requester_id, action_type, resource_id, arguments,
                        resource_version, policy_version, status, expires_at)
                       VALUES (%s, %s, 'CREATE_PURCHASE_ORDER', %s, %s, %s, %s, 'PENDING', %s)""",
                    (
                        approval_id,
                        principal.principal_id,
                        requisition_id,
                        Jsonb(arguments),
                        context.row_version,
                        decision.policy_version,
                        expires_at,
                    ),
                )
                self._write_audit(
                    connection,
                    principal.principal_id,
                    "REQUEST_APPROVAL",
                    requisition_id,
                    "PENDING",
                    decision.reason_codes,
                    decision.policy_version,
                    {"approval_id": str(approval_id)},
                )
        return ApprovalRequestResult(
            approval_id=approval_id,
            requisition_id=requisition_id,
            status="PENDING",
            expires_at=expires_at.isoformat(),
            policy_version=decision.policy_version,
            reason_codes=decision.reason_codes,
        )

    def decide_approval(
        self,
        approval_id: UUID,
        principal: PrincipalContext,
        request: ApprovalDecisionRequest,
    ) -> ApprovalRequestResult:
        if not {"MANAGER", "ADMIN"}.intersection(principal.roles):
            raise TransactionError("Manager approval permission is required", status_code=403)
        expired = False
        invalidated = False
        with psycopg.connect(
            self._settings.database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            with connection.transaction():
                approval: Any = connection.execute(
                    """SELECT approval_id, requester_id, action_type, resource_id, arguments,
                              resource_version, policy_version, status, expires_at
                       FROM approval_request WHERE approval_id = %s FOR UPDATE""",
                    (approval_id,),
                ).fetchone()
                if approval is None:
                    raise TransactionError("Approval request not found", status_code=404)
                if approval["status"] != "PENDING":
                    raise TransactionError("Approval request is no longer pending")
                if approval["requester_id"] == principal.principal_id:
                    raise TransactionError(
                        "Requesters cannot approve their own actions", status_code=403
                    )
                context = self._load_context(
                    connection, str(approval["resource_id"]), principal, lock=True
                )
                if context.row_version != int(approval["resource_version"]):
                    connection.execute(
                        "UPDATE approval_request SET status = 'INVALIDATED' WHERE approval_id = %s",
                        (approval_id,),
                    )
                    invalidated = True
                elif datetime.now(timezone.utc) >= approval["expires_at"]:
                    connection.execute(
                        "UPDATE approval_request SET status = 'EXPIRED' WHERE approval_id = %s",
                        (approval_id,),
                    )
                    expired = True
                    status: ApprovalStatus = "EXPIRED"
                elif not invalidated:
                    status = "APPROVED" if request.approved else "REJECTED"
                    connection.execute(
                        """UPDATE approval_request
                           SET status = %s, approver_id = %s, decided_at = now()
                           WHERE approval_id = %s""",
                        (status, principal.principal_id, approval_id),
                    )
                    self._write_audit(
                        connection,
                        principal.principal_id,
                        "DECIDE_APPROVAL",
                        str(approval["resource_id"]),
                        status,
                        [],
                        str(approval["policy_version"]),
                        {"approval_id": str(approval_id), "note": request.note},
                    )
        if invalidated:
            raise TransactionError("Requisition changed after approval was requested")
        if expired:
            return ApprovalRequestResult(
                approval_id=approval_id,
                requisition_id=str(approval["resource_id"]),
                status="EXPIRED",
                expires_at=approval["expires_at"].isoformat(),
                policy_version=str(approval["policy_version"]),
                reason_codes=["APPROVAL_EXPIRED"],
            )
        return ApprovalRequestResult(
            approval_id=approval_id,
            requisition_id=str(approval["resource_id"]),
            status=status,
            expires_at=approval["expires_at"].isoformat(),
            policy_version=str(approval["policy_version"]),
            reason_codes=[],
        )

    def create_purchase_order(
        self,
        requisition_id: str,
        principal: PrincipalContext,
        idempotency_key: str,
        command: PurchaseOrderCommand,
    ) -> PurchaseOrderExecution:
        if not idempotency_key or len(idempotency_key) > 200:
            raise TransactionError("A valid idempotency key is required", status_code=400)
        if "AUDITOR" in principal.roles:
            raise TransactionError("Auditors cannot create purchase orders", status_code=403)
        if self._settings.dry_run:
            simulation = self.simulate(requisition_id, principal)
            status: PurchaseOrderStatus = (
                "SIMULATED"
                if simulation.available
                else "APPROVAL_REQUIRED"
                if simulation.approval_required
                else "DENIED"
            )
            return PurchaseOrderExecution(
                requisition_id=requisition_id,
                status=status,
                dry_run=True,
                decision=simulation.decision,
            )

        scope = f"requisition:{requisition_id}:CREATE_PURCHASE_ORDER"
        request_hash = self._purchase_order_request_hash(scope, command)
        with psycopg.connect(
            self._settings.database_url, connect_timeout=3, row_factory=dict_row
        ) as connection:
            with connection.transaction():
                existing: Any = connection.execute(
                    """SELECT request_hash, status, response_payload
                       FROM idempotency_record
                       WHERE principal_id = %s AND action_scope = %s AND idempotency_key = %s
                       FOR UPDATE""",
                    (principal.principal_id, scope, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise TransactionError("Idempotency key was used for another operation")
                    if existing["status"] == "SUCCEEDED" and existing["response_payload"]:
                        prior = PurchaseOrderExecution.model_validate(existing["response_payload"])
                        return prior.model_copy(update={"idempotent_replay": True})
                    raise TransactionError("An operation with this idempotency key is in progress")

                inserted = connection.execute(
                    """INSERT INTO idempotency_record
                       (principal_id, action_scope, idempotency_key, request_hash, status)
                       VALUES (%s, %s, %s, %s, 'IN_PROGRESS')
                       ON CONFLICT (principal_id, action_scope, idempotency_key) DO NOTHING""",
                    (principal.principal_id, scope, idempotency_key, request_hash),
                )
                if inserted.rowcount == 0:
                    replay: Any = connection.execute(
                        """SELECT request_hash, status, response_payload
                           FROM idempotency_record
                           WHERE principal_id = %s AND action_scope = %s AND idempotency_key = %s
                           FOR UPDATE""",
                        (principal.principal_id, scope, idempotency_key),
                    ).fetchone()
                    if (
                        replay
                        and replay["request_hash"] == request_hash
                        and replay["status"] == "SUCCEEDED"
                    ):
                        prior = PurchaseOrderExecution.model_validate(replay["response_payload"])
                        return prior.model_copy(update={"idempotent_replay": True})
                    raise TransactionError("An operation with this idempotency key is in progress")
                context = self._load_context(connection, requisition_id, principal, lock=True)
                decision = self._evaluate(context, principal)
                manager_approval_exists = False
                if decision.approval_required:
                    manager_approval_exists = self._validate_approval(
                        connection,
                        command.approval_id,
                        context,
                        principal,
                        decision,
                    )
                    decision = self._evaluate(
                        context, principal, manager_approval_exists=manager_approval_exists
                    )
                if not decision.allowed:
                    raise TransactionError(
                        "Purchase order is not permitted: "
                        + (", ".join(decision.reason_codes) or "POLICY_DENIED")
                    )

                purchase_order_id = f"PO-{uuid4().hex[:12].upper()}"
                connection.execute(
                    """INSERT INTO purchase_order
                       (purchase_order_id, requisition_id, source_supplier_id,
                        canonical_supplier_id, buyer_id, amount, currency, status,
                        data_quality_issues, source_system)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'OPEN', '[]'::jsonb,
                               'CONTEXT_PLATFORM')""",
                    (
                        purchase_order_id,
                        requisition_id,
                        context.supplier_source_id,
                        context.canonical_supplier_id,
                        context.buyer_id,
                        context.amount,
                        context.currency,
                    ),
                )
                updated = connection.execute(
                    """UPDATE purchase_requisition
                       SET state = 'CONVERTED', row_version = row_version + 1, updated_at = now()
                       WHERE requisition_id = %s AND row_version = %s AND state = 'APPROVED'""",
                    (requisition_id, context.row_version),
                )
                if updated.rowcount != 1:
                    raise TransactionError("Requisition changed before the PO could be committed")

                connection.execute(
                    """INSERT INTO projection_outbox
                       (event_id, aggregate_type, aggregate_id, event_type, payload)
                       VALUES (%s, 'PurchaseOrder', %s, 'PURCHASE_ORDER_CREATED', %s)""",
                    (
                        uuid4(),
                        purchase_order_id,
                        Jsonb(
                            {
                                "purchase_order_id": purchase_order_id,
                                "requisition_id": requisition_id,
                                "canonical_supplier_id": context.canonical_supplier_id,
                                "source_supplier_id": context.supplier_source_id,
                                "buyer_id": context.buyer_id,
                                "amount": str(context.amount),
                                "currency": context.currency,
                            }
                        ),
                    ),
                )

                result = PurchaseOrderExecution(
                    requisition_id=requisition_id,
                    purchase_order_id=purchase_order_id,
                    status="CREATED",
                    dry_run=False,
                    decision=decision,
                )
                connection.execute(
                    """UPDATE idempotency_record
                       SET status = 'SUCCEEDED', response_payload = %s
                       WHERE principal_id = %s AND action_scope = %s AND idempotency_key = %s""",
                    (
                        Jsonb(result.model_dump(mode="json")),
                        principal.principal_id,
                        scope,
                        idempotency_key,
                    ),
                )
                self._write_audit(
                    connection,
                    principal.principal_id,
                    "CREATE_PURCHASE_ORDER",
                    requisition_id,
                    "CREATED",
                    decision.reason_codes,
                    decision.policy_version,
                    {"purchase_order_id": purchase_order_id},
                )
                return result

    def _load_context(
        self,
        connection: psycopg.Connection[Any],
        requisition_id: str,
        principal: PrincipalContext,
        *,
        lock: bool = False,
    ) -> RequisitionContext:
        if not {"BUYER", "MANAGER", "ADMIN"}.intersection(principal.roles):
            raise TransactionError("Purchasing access is required", status_code=403)
        global_scope = "ADMIN" in principal.roles
        scope = "" if global_scope else "AND business_unit_id = ANY(%s)"
        parameters: tuple[Any, ...] = (requisition_id,)
        if not global_scope:
            parameters += (principal.business_unit_ids,)
        lock_clause = "FOR UPDATE" if lock else ""
        row: Any = connection.execute(
            f"""SELECT requisition_id, source_supplier_id, canonical_supplier_id,
                      buyer_id, business_unit_id, state, amount, currency, product_ids, row_version
               FROM purchase_requisition
               WHERE requisition_id = %s {scope}
               {lock_clause}""",
            parameters,
        ).fetchone()
        if row is None:
            raise TransactionError(
                "Requisition not found or outside principal scope", status_code=404
            )

        supplier: Any = None
        if row["canonical_supplier_id"]:
            supplier = connection.execute(
                """SELECT canonical_entity_id, preferred_name, status, risk_rating,
                          approved_categories
                   FROM canonical_supplier
                   WHERE canonical_entity_id = %s
                   FOR SHARE""",
                (row["canonical_supplier_id"],),
            ).fetchone()
        product_ids = [str(product_id) for product_id in row["product_ids"]]
        product_rows = (
            connection.execute(
                "SELECT DISTINCT category FROM product WHERE product_id = ANY(%s)",
                (product_ids,),
            ).fetchall()
            if product_ids
            else []
        )
        categories = sorted({str(item["category"]) for item in product_rows})
        contract_rows = (
            connection.execute(
                """SELECT contract_id FROM contract
                   WHERE canonical_supplier_id = %s AND status = 'ACTIVE'
                     AND start_date <= %s AND end_date >= %s""",
                (row["canonical_supplier_id"], date.today(), date.today()),
            ).fetchall()
            if row["canonical_supplier_id"]
            else []
        )
        buyer: Any = connection.execute(
            "SELECT name FROM buyer WHERE buyer_id = %s", (row["buyer_id"],)
        ).fetchone()
        return RequisitionContext(
            requisition_id=str(row["requisition_id"]),
            supplier_source_id=str(row["source_supplier_id"]),
            canonical_supplier_id=(
                str(row["canonical_supplier_id"]) if row["canonical_supplier_id"] else None
            ),
            buyer_id=str(row["buyer_id"]),
            buyer_name=str(buyer["name"]) if buyer else "Unknown buyer",
            business_unit_id=str(row["business_unit_id"]),
            state=str(row["state"]),
            amount=Decimal(row["amount"]),
            currency=str(row["currency"]),
            product_ids=product_ids,
            categories=categories,
            supplier=SupplierContext(
                canonical_entity_id=(str(supplier["canonical_entity_id"]) if supplier else None),
                preferred_name=str(supplier["preferred_name"]) if supplier else None,
                status=str(supplier["status"]) if supplier else "UNKNOWN",
                risk_rating=str(supplier["risk_rating"]) if supplier else "UNKNOWN",
                approved_categories=(list(supplier["approved_categories"]) if supplier else []),
            ),
            active_contract_ids=[str(item["contract_id"]) for item in contract_rows],
            row_version=int(row["row_version"]),
        )

    def _evaluate(
        self,
        context: RequisitionContext,
        principal: PrincipalContext,
        *,
        manager_approval_exists: bool = False,
    ) -> PolicyDecision:
        policy_input = build_create_po_policy_input(
            context,
            principal,
            manager_approval_exists=manager_approval_exists,
        )
        if self._policy_evaluator is not None:
            return self._policy_evaluator(policy_input)
        return OPAClient(self._settings.opa_url).evaluate(policy_input)

    @staticmethod
    def _purchase_order_request_hash(scope: str, command: PurchaseOrderCommand) -> str:
        payload = {
            "scope": scope,
            "approval_id": str(command.approval_id) if command.approval_id else None,
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _approval_arguments(context: RequisitionContext) -> dict[str, Any]:
        return {
            "action": "CREATE_PURCHASE_ORDER",
            "requisition_id": context.requisition_id,
            "supplier_id": context.canonical_supplier_id,
            "amount": str(context.amount),
            "currency": context.currency,
            "categories": context.categories,
        }

    def _validate_approval(
        self,
        connection: psycopg.Connection[Any],
        approval_id: UUID | None,
        context: RequisitionContext,
        principal: PrincipalContext,
        decision: PolicyDecision,
    ) -> bool:
        if approval_id is None:
            return False
        approval: Any = connection.execute(
            """SELECT requester_id, action_type, resource_id, arguments, resource_version,
                      policy_version, status, expires_at
               FROM approval_request WHERE approval_id = %s FOR UPDATE""",
            (approval_id,),
        ).fetchone()
        if approval is None or approval["status"] != "APPROVED":
            return False
        if approval["requester_id"] != principal.principal_id:
            raise TransactionError(
                "Approval request does not belong to the requesting principal",
                status_code=403,
            )
        if (
            approval["action_type"] != "CREATE_PURCHASE_ORDER"
            or approval["resource_id"] != context.requisition_id
        ):
            raise TransactionError("Approval request does not match this purchase order")
        if int(approval["resource_version"]) != context.row_version:
            raise TransactionError("Approval request was invalidated by a requisition change")
        if approval["policy_version"] != decision.policy_version:
            raise TransactionError("Policy changed after the approval request")
        if datetime.now(timezone.utc) >= approval["expires_at"]:
            raise TransactionError("Approval request has expired")
        if approval["arguments"] != self._approval_arguments(context):
            raise TransactionError("Approval does not match the requested PO arguments")
        return True

    @staticmethod
    def _write_audit(
        connection: psycopg.Connection[Any],
        principal_id: str,
        action_type: str,
        resource_id: str,
        outcome: str,
        reason_codes: list[str],
        policy_version: str,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            """INSERT INTO audit_event
               (event_id, request_id, actor_id, action_type, resource_type, resource_id,
                outcome, reason_codes, policy_version, details)
               VALUES (%s, %s, %s, %s, 'PurchaseRequisition', %s, %s, %s, %s, %s)""",
            (
                uuid4(),
                current_request_id(),
                principal_id,
                action_type,
                resource_id,
                outcome,
                Jsonb(reason_codes),
                policy_version,
                Jsonb({**details, "trace_id": current_trace_id()}),
            ),
        )
