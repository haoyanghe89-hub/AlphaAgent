"""Short HTTP traces link to independently lived background business traces."""

from .tracing import observation


def install_http_tracing(app):
    @app.middleware("http")
    async def trace_request(request, call_next):
        if request.method not in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        } or not request.url.path.startswith("/api/quant/"):
            return await call_next(request)
        with observation("http.request", metadata={"method": request.method}) as current:
            response = await call_next(request)
            route = request.scope.get("route")
            current.update(
                output={"http_status": response.status_code},
                metadata={"route": getattr(route, "path", "unmatched")},
            )
            if current.trace_id:
                response.headers["X-Langfuse-Request-Trace-Id"] = current.trace_id
            return response
