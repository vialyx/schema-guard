"""Read-only order endpoints."""

from typing import Iterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Order
from app.services.reconciliation import order_value_cents

router = APIRouter(prefix="/orders", tags=["orders"])


def get_session() -> Iterator[Session]:
    """Stub dependency. The real app wires this to a sessionmaker at startup."""
    raise NotImplementedError("get_session must be overridden by the application")


def _to_dict(order: Order) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "supplier_id": order.supplier_id,
        "material_grade": order.material_grade,
        "net_weight_kg": str(order.net_weight_kg),
        "price_per_kg_cents": order.price_per_kg_cents,
        "value_cents": order_value_cents(order),
        "status": order.status,
        "created_at": order.created_at.isoformat() if order.created_at else None,
    }


@router.get("/{order_no}")
def get_order(order_no: str, session: Session = Depends(get_session)) -> dict:
    order = session.scalar(select(Order).where(Order.order_no == order_no))
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return _to_dict(order)


@router.get("")
def list_orders(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[dict]:
    stmt = select(Order).order_by(Order.created_at.desc(), Order.id.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Order.status == status)
    return [_to_dict(o) for o in session.scalars(stmt)]
