import importlib
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
EXTENSION_PACKAGE = 'bypass'

_loaded = False
_module = None


def get_extension():
    """Load the optional bypass extension package from the project root.

    Returns None when the folder is absent. The package's __init__ must not
    import heavy dependencies at import time.
    """
    global _loaded, _module
    if not _loaded:
        _loaded = True
        if BASE_DIR not in sys.path:
            sys.path.insert(0, BASE_DIR)
        try:
            _module = importlib.import_module(EXTENSION_PACKAGE)
        except ImportError:
            _module = None
    return _module
