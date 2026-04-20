"""dvm-eagraph — LeanIX → Neo4j graph loader."""

try:
    from dvm_eagraph._version import __version__
except ImportError:
    # Package not installed (e.g. running from source without build)
    __version__ = "0.0.0.dev0"
