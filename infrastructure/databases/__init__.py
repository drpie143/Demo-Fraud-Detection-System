"""Database integrations.

Submodules intentionally own their connection setup. This package initializer
stays side-effect free so importing one helper does not connect to every cloud
service.
"""
