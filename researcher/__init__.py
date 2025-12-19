try:
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version("research_agent")
except Exception:
    __version__ = "0.1.0"
