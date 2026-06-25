"""Register aiohttp HTTP routes for the shop API."""

from __future__ import annotations

from aiohttp import web


def register_http_routes(http_app: web.Application, handlers) -> None:
    """Attach all HTTP handlers to the aiohttp application."""
    http_app.router.add_post("/order", handlers.handle_order)
    http_app.router.add_route("OPTIONS", "/order", handlers.handle_options)
    http_app.router.add_post("/check_coupon", handlers.handle_check_coupon)
    http_app.router.add_route("OPTIONS", "/check_coupon", handlers.handle_options)
    http_app.router.add_route("OPTIONS", "/api/{tail:.*}", handlers.handle_options)

    http_app.router.add_get("/health", handlers.handle_health)
    http_app.router.add_get("/admin/panel", handlers.handle_admin_panel)
    http_app.router.add_get("/api/products", handlers.handle_get_products)
    http_app.router.add_get("/api/categories", handlers.handle_get_categories)
    http_app.router.add_get("/api/filaments", handlers.handle_get_filaments)
    http_app.router.add_get("/api/products/{id}", handlers.handle_get_product)
    http_app.router.add_post("/api/categories", handlers.handle_create_category)
    http_app.router.add_put("/api/categories/{id}", handlers.handle_update_category)
    http_app.router.add_delete("/api/categories/{id}", handlers.handle_delete_category)
    http_app.router.add_put("/api/filaments/{id}", handlers.handle_update_filament)
    http_app.router.add_post("/api/products", handlers.handle_create_product)
    http_app.router.add_put("/api/products/{id}", handlers.handle_update_product)
    http_app.router.add_delete("/api/products/{id}", handlers.handle_delete_product)
    http_app.router.add_get("/api/orders", handlers.handle_get_orders)
    http_app.router.add_get("/api/orders/{id}", handlers.handle_get_order)
    http_app.router.add_put("/api/orders/{id}/pricing", handlers.handle_update_order_pricing)
    http_app.router.add_put("/api/orders/{id}/status", handlers.handle_update_order_status)
    http_app.router.add_delete("/api/orders/{id}", handlers.handle_delete_order)
    http_app.router.add_get("/api/coupons", handlers.handle_get_coupons)
    http_app.router.add_post("/api/coupons", handlers.handle_create_coupon)
    http_app.router.add_put("/api/coupons/{code}", handlers.handle_update_coupon)
    http_app.router.add_delete("/api/coupons/{code}", handlers.handle_delete_coupon)
    http_app.router.add_post("/api/upload-photo", handlers.handle_upload_photo)
    http_app.router.add_post("/api/upload-photo-url", handlers.handle_upload_photo_url)

    http_app.router.add_get("/", handlers.handle_index)
    http_app.router.add_get("/{path:.*}", handlers.handle_static)
