from uuid import uuid4

from enterprise_context.config import Settings
from enterprise_context.domain.transactions import ProcurementTransactions
from enterprise_context.domain.write_models import PurchaseOrderCommand


def test_idempotency_hash_binds_to_exact_approval_argument() -> None:
    service = ProcurementTransactions(Settings())
    first_approval = PurchaseOrderCommand(approval_id=uuid4())
    different_approval = PurchaseOrderCommand(approval_id=uuid4())
    scope = "requisition:PR-1007:CREATE_PURCHASE_ORDER"

    assert service._purchase_order_request_hash(scope, first_approval) == (
        service._purchase_order_request_hash(scope, first_approval)
    )
    assert service._purchase_order_request_hash(scope, first_approval) != (
        service._purchase_order_request_hash(scope, different_approval)
    )
