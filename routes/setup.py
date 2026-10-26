"""Register aiohttp HTTP routes for the shop API."""

from __future__ import annotations

from aiohttp import web

# Стискаємо текстові відповіді: каталог /api/products — ~80 КБ JSON, у gzip ~12 КБ
_COMPRESSIBLE_TYPES = ("application/json", "text/html", "text/csv", "text/plain", "application/javascript", "text/css")
_MIN_COMPRESS_BYTES = 1024


@web.middleware
async def compression_middleware(request: web.Request, handler):
    response = await handler(request)
    if (
        type(response) is web.Response
        and "gzip" in request.headers.get("Accept-Encoding", "").lower()
        and response.content_type in _COMPRESSIBLE_TYPES
        and isinstance(response.body, (bytes, bytearray))
        and len(response.body) >= _MIN_COMPRESS_BYTES
        and not response.headers.get("Content-Encoding")
    ):
        response.enable_compression(web.ContentCoding.gzip)
    return response


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    response = await handler(request)
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    return response


def register_http_routes(http_app: web.Application, handlers) -> None:
    """Attach all HTTP handlers to the aiohttp application."""
    http_app.middlewares.append(security_headers_middleware)
    http_app.middlewares.append(compression_middleware)
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
    http_app.router.add_get("/api/products/{id}/reviews", handlers.handle_get_product_reviews)
    http_app.router.add_get("/api/products/{id}/related", handlers.handle_get_product_related)
    http_app.router.add_post("/api/categories", handlers.handle_create_category)
    # Статичний шлях раніше за /api/categories/{id}, інакше «order» зловить {id}
    http_app.router.add_put("/api/categories/order", handlers.handle_reorder_categories)
    http_app.router.add_put("/api/categories/{id}", handlers.handle_update_category)
    http_app.router.add_delete("/api/categories/{id}", handlers.handle_delete_category)
    http_app.router.add_post("/api/filaments", handlers.handle_create_filament)
    http_app.router.add_put("/api/filaments/{id}", handlers.handle_update_filament)
    http_app.router.add_delete("/api/filaments/{id}", handlers.handle_delete_filament)
    http_app.router.add_post("/api/products", handlers.handle_create_product)
    http_app.router.add_put("/api/products/{id}", handlers.handle_update_product)
    http_app.router.add_delete("/api/products/{id}", handlers.handle_delete_product)
    http_app.router.add_patch("/api/products/{id}", handlers.handle_patch_product)
    http_app.router.add_put("/api/products/{id}/status", handlers.handle_set_product_status)
    http_app.router.add_post("/api/products/{id}/duplicate", handlers.handle_duplicate_product)
    http_app.router.add_post("/api/products/bulk", handlers.handle_bulk_products)
    http_app.router.add_get("/api/admin/products", handlers.handle_get_admin_products)
    http_app.router.add_get("/my/orders", handlers.handle_get_my_orders)
    http_app.router.add_get("/my/reviews/pending", handlers.handle_get_pending_reviews)
    http_app.router.add_post("/my/review-photo", handlers.handle_upload_review_photo)
    http_app.router.add_post("/my/share-product", handlers.handle_prepare_share)
    http_app.router.add_get("/my/prizes", handlers.handle_get_my_prizes)
    http_app.router.add_post("/my/prize-card", handlers.handle_upload_prize_card)
    http_app.router.add_post("/my/share-prize", handlers.handle_prepare_prize_share)
    http_app.router.add_post("/reviews", handlers.handle_post_review)
    http_app.router.add_route("OPTIONS", "/my/{tail:.*}", handlers.handle_options)
    http_app.router.add_route("OPTIONS", "/reviews", handlers.handle_options)
    http_app.router.add_route("OPTIONS", "/my/orders", handlers.handle_options)
    http_app.router.add_get("/api/orders", handlers.handle_get_orders)
    http_app.router.add_get("/api/orders/counts", handlers.handle_get_order_counts)
    http_app.router.add_get("/api/orders.csv", handlers.handle_export_orders_csv)
    http_app.router.add_post("/api/orders", handlers.handle_create_manual_order)
    http_app.router.add_post("/api/orders/{id}/message", handlers.handle_send_order_message)
    http_app.router.add_get("/api/print-queue", handlers.handle_get_print_queue)
    http_app.router.add_get("/api/admin/overview", handlers.handle_get_overview)
    http_app.router.add_put("/api/order-items/{id}/printed", handlers.handle_set_item_printed)
    http_app.router.add_get("/api/orders/{id}", handlers.handle_get_order)
    http_app.router.add_put("/api/orders/{id}", handlers.handle_update_order)
    http_app.router.add_put("/api/orders/{id}/pricing", handlers.handle_update_order_pricing)
    http_app.router.add_put("/api/orders/{id}/status", handlers.handle_update_order_status)
    http_app.router.add_delete("/api/orders/{id}", handlers.handle_delete_order)
    http_app.router.add_delete("/api/orders/{id}/status-messages", handlers.handle_delete_status_messages)
    http_app.router.add_get("/api/coupons", handlers.handle_get_coupons)
    http_app.router.add_post("/api/coupons", handlers.handle_create_coupon)
    http_app.router.add_put("/api/coupons/{code}", handlers.handle_update_coupon)
    http_app.router.add_delete("/api/coupons/{code}", handlers.handle_delete_coupon)
    http_app.router.add_post("/api/coupons/{code}/allowed-users", handlers.handle_add_coupon_users)
    http_app.router.add_delete(
        "/api/coupons/{code}/allowed-users/{user_id}", handlers.handle_remove_coupon_user
    )
    http_app.router.add_get("/api/promotions", handlers.handle_get_public_promotions)
    http_app.router.add_get("/api/config", handlers.handle_get_config)
    http_app.router.add_get("/delivery/np/cities", handlers.handle_np_cities)
    http_app.router.add_get("/delivery/np/warehouses", handlers.handle_np_warehouses)
    http_app.router.add_route("OPTIONS", "/delivery/{tail:.*}", handlers.handle_options)
    http_app.router.add_get("/api/hits", handlers.handle_get_hits)
    http_app.router.add_get("/api/collections", handlers.handle_get_collections)
    http_app.router.add_get("/api/admin/collections", handlers.handle_get_admin_collections)
    http_app.router.add_post("/api/admin/collections", handlers.handle_create_collection)
    # Статичні шляхи раніше за /{id}, інакше «order» і «preview» зловить {id}
    http_app.router.add_put("/api/admin/collections/order", handlers.handle_reorder_collections)
    http_app.router.add_post("/api/admin/collections/preview", handlers.handle_preview_collection)
    http_app.router.add_put("/api/admin/collections/{id}", handlers.handle_update_collection)
    http_app.router.add_delete("/api/admin/collections/{id}", handlers.handle_delete_collection)
    http_app.router.add_put("/api/admin/collections/{id}/products", handlers.handle_set_collection_products)
    http_app.router.add_get("/api/admin/products/{id}/collections", handlers.handle_get_product_collections)
    http_app.router.add_put("/api/admin/products/{id}/collections", handlers.handle_set_product_collections)
    http_app.router.add_get("/api/admin/products/{id}/channel-posts", handlers.handle_get_channel_posts)
    http_app.router.add_post("/api/admin/products/{id}/channel-posts", handlers.handle_publish_channel_post)
    http_app.router.add_get("/api/admin/reviews", handlers.handle_get_admin_reviews)
    http_app.router.add_post("/api/admin/reviews/bulk", handlers.handle_bulk_admin_reviews)
    http_app.router.add_put("/api/admin/reviews/{id}/status", handlers.handle_set_admin_review_status)
    http_app.router.add_put("/api/admin/reviews/{id}/reply", handlers.handle_set_admin_review_reply)
    http_app.router.add_delete("/api/admin/reviews/{id}", handlers.handle_delete_admin_review)
    http_app.router.add_get("/api/search/popular", handlers.handle_get_popular_searches)
    http_app.router.add_post("/api/events", handlers.handle_post_event)
    http_app.router.add_get("/api/admin/users", handlers.handle_get_users)
    http_app.router.add_get("/api/admin/users.csv", handlers.handle_export_users_csv)
    http_app.router.add_post("/api/admin/users/{id}/coupon", handlers.handle_create_personal_coupon)
    http_app.router.add_post("/api/admin/users/{id}/message", handlers.handle_send_user_message)
    http_app.router.add_get("/api/admin/broadcasts", handlers.handle_get_broadcasts)
    http_app.router.add_post("/api/admin/broadcasts", handlers.handle_create_broadcast)
    # Статичний шлях раніше за /{id}
    http_app.router.add_post("/api/admin/broadcasts/preview", handlers.handle_preview_broadcast)
    http_app.router.add_delete("/api/admin/broadcasts/{id}", handlers.handle_delete_broadcast)
    http_app.router.add_get("/api/admin/giveaways", handlers.handle_get_giveaways)
    http_app.router.add_post("/api/admin/giveaways", handlers.handle_create_giveaway)
    http_app.router.add_post("/api/admin/giveaways/{id}/draw", handlers.handle_draw_giveaway)
    http_app.router.add_post("/api/admin/giveaways/{id}/publish", handlers.handle_publish_giveaway)
    http_app.router.add_post("/api/admin/giveaways/{id}/cancel", handlers.handle_cancel_giveaway)
    http_app.router.add_post(
        "/api/admin/giveaways/{id}/winners/{winner_id}/reroll", handlers.handle_reroll_giveaway_winner
    )
    http_app.router.add_post(
        "/api/admin/giveaways/{id}/winners/{winner_id}/extend", handlers.handle_extend_giveaway_winner
    )
    http_app.router.add_get("/api/admin/whoami", handlers.handle_admin_whoami)
    http_app.router.add_get("/api/admin/ai-suggestions", handlers.handle_get_ai_suggestions)
    # Статичний шлях раніше за /{id}
    http_app.router.add_post("/api/admin/ai-suggestions/generate", handlers.handle_generate_ai_suggestions)
    http_app.router.add_post("/api/admin/ai-suggestions/photos", handlers.handle_ai_suggestion_photos)
    http_app.router.add_post("/api/admin/ai-suggestions/manual", handlers.handle_add_ai_suggestion)
    http_app.router.add_put("/api/admin/ai-suggestions/{id}", handlers.handle_update_ai_suggestion)
    http_app.router.add_delete("/api/admin/ai-suggestions/{id}", handlers.handle_delete_ai_suggestion)
    http_app.router.add_get("/api/admin/settings", handlers.handle_get_settings)
    http_app.router.add_put("/api/admin/settings", handlers.handle_update_settings)
    http_app.router.add_get("/api/admin/promotions", handlers.handle_get_promotions)
    http_app.router.add_post("/api/admin/promotions", handlers.handle_create_promotion)
    http_app.router.add_put("/api/admin/promotions/{id}", handlers.handle_update_promotion)
    http_app.router.add_delete("/api/admin/promotions/{id}", handlers.handle_delete_promotion)
    http_app.router.add_post("/api/upload-photo", handlers.handle_upload_photo)
    http_app.router.add_post("/api/upload-photo-url", handlers.handle_upload_photo_url)
    http_app.router.add_post("/api/upload-video", handlers.handle_upload_video)

    http_app.router.add_get("/", handlers.handle_index)
    http_app.router.add_get("/{path:.*}", handlers.handle_static)
