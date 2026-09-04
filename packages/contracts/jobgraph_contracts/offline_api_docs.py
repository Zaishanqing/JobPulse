"""Serve FastAPI's interactive API documentation without public CDNs."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.staticfiles import StaticFiles


_ASSET_DIRECTORY = Path(__file__).resolve().parent / "api_docs_assets"
_ASSET_URL = "/_api-docs-assets"
_REQUIRED_ASSETS = (
    "favicon-32x32.png",
    "manifest.json",
    "redoc.standalone.js",
    "swagger-ui-bundle.js",
    "swagger-ui.css",
)


def install_offline_api_docs(
    app: FastAPI,
    *,
    docs_url: str = "/docs",
    redoc_url: str = "/redoc",
) -> None:
    """Install local Swagger UI and ReDoc routes on an opted-out FastAPI app."""

    if app.docs_url is not None or app.redoc_url is not None:
        raise RuntimeError(
            "FastAPI must be created with docs_url=None and redoc_url=None "
            "before installing offline API docs."
        )
    if app.openapi_url is None:
        raise RuntimeError("Offline API docs require an OpenAPI endpoint.")

    missing = [name for name in _REQUIRED_ASSETS if not (_ASSET_DIRECTORY / name).is_file()]
    if missing:
        raise RuntimeError(f"Offline API documentation assets are missing: {', '.join(missing)}")

    app.mount(
        _ASSET_URL,
        StaticFiles(directory=str(_ASSET_DIRECTORY), check_dir=True),
        name="offline-api-docs-assets",
    )

    @app.get(docs_url, include_in_schema=False)
    async def offline_swagger_ui():
        swagger_parameters = dict(app.swagger_ui_parameters or {})
        swagger_parameters["validatorUrl"] = None
        return get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            init_oauth=app.swagger_ui_init_oauth,
            swagger_ui_parameters=swagger_parameters,
            swagger_js_url=f"{_ASSET_URL}/swagger-ui-bundle.js",
            swagger_css_url=f"{_ASSET_URL}/swagger-ui.css",
            swagger_favicon_url=f"{_ASSET_URL}/favicon-32x32.png",
        )

    if app.swagger_ui_oauth2_redirect_url:

        @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
        async def swagger_ui_redirect():
            return get_swagger_ui_oauth2_redirect_html()

    @app.get(redoc_url, include_in_schema=False)
    async def offline_redoc():
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - ReDoc",
            redoc_js_url=f"{_ASSET_URL}/redoc.standalone.js",
            with_google_fonts=False,
            redoc_favicon_url=f"{_ASSET_URL}/favicon-32x32.png",
        )


__all__ = ["install_offline_api_docs"]
