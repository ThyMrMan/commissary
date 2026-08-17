"""WSGI entrypoint for Commissary production deployments."""

from web_server import app, start_runtime_services


start_runtime_services()

application = app
