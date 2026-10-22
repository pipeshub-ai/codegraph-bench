"""The business domains of the fixture app, shared by every generator.

The app is "acme-shop", a mid-sized e-commerce monorepo. Every domain shows up
in the Python backend, the TypeScript frontend and the JS worker, so the
generic method names (get, list, save, create, load, run) collide across
dozens of files the way they do in real code.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    key: str            # package / folder name: "orders"
    entity: str         # class name: "Order"
    fields: tuple[str, ...]
    validator: str      # shared validation helper the service calls

    @property
    def var(self) -> str:
        """snake_case singular: "order", "inventory_item"."""
        out = []
        for i, ch in enumerate(self.entity):
            if ch.isupper() and i:
                out.append("_")
            out.append(ch.lower())
        return "".join(out)

    @property
    def camel(self) -> str:
        return self.entity[0].lower() + self.entity[1:]

    @property
    def title(self) -> str:
        return self.key.capitalize()


DOMAINS: tuple[Domain, ...] = (
    Domain("users", "User", ("email", "name"), "validate_email"),
    Domain("orders", "Order", ("customer_id", "total"), "validate_amount"),
    Domain("payments", "Payment", ("order_id", "amount"), "validate_amount"),
    Domain("inventory", "InventoryItem", ("sku", "quantity"), "validate_sku"),
    Domain("catalog", "Product", ("sku", "title"), "validate_sku"),
    Domain("shipping", "Shipment", ("order_id", "carrier"), "validate_required"),
    Domain("reviews", "Review", ("product_id", "rating"), "validate_rating"),
    Domain("notifications", "Notification", ("user_id", "channel"), "validate_required"),
    Domain("auth", "Session", ("user_id", "token"), "validate_required"),
    Domain("billing", "Invoice", ("customer_id", "amount"), "validate_amount"),
    Domain("analytics", "Metric", ("name", "value"), "validate_required"),
    Domain("search", "SearchQuery", ("text", "limit"), "validate_required"),
    Domain("coupons", "Coupon", ("code", "percent"), "validate_percent"),
    Domain("returns", "ReturnRequest", ("order_id", "reason"), "validate_required"),
)

# Added by mutation step 4.
WISHLIST = Domain("wishlist", "WishlistItem", ("user_id", "product_id"), "validate_required")


def active_domains(step: int) -> tuple[Domain, ...]:
    return DOMAINS + ((WISHLIST,) if step >= 4 else ())


# Mutation steps, applied cumulatively on top of step 0. Each generator honours
# the steps that touch its part of the tree.
STEPS = {
    0: "baseline",
    1: "rename shared validation.require -> require_fields and update every caller",
    2: "delete backend coupons/events.py and its use in coupons/service.py",
    3: "move frontend src/lib/format.ts -> src/lib/text/format.ts and update importers",
    4: "add the wishlist domain (backend + frontend)",
    5: "remove Cache.invalidate and the callers that used it",
    6: "empty out backend analytics/legacy_metrics.py",
    7: "move Page/paginate to shared/paging.py, leaving a re-export in shared/pagination.py",
}
