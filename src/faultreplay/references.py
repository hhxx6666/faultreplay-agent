from importlib import import_module

from .replay import Adapter, InputError


def load_reference(reference: str) -> object:
    module, separator, attribute = reference.partition(":")
    if not separator or not module or not attribute.isidentifier():
        raise InputError(f"expected module:attribute, got {reference!r}")
    try:
        return getattr(import_module(module), attribute)
    except Exception as exc:
        raise InputError(f"could not load {reference!r}: {type(exc).__name__}: {exc}") from exc


def load_adapter(reference: str) -> Adapter:
    adapter = load_reference(reference)
    if not isinstance(adapter, Adapter):
        raise InputError(f"{reference!r} must refer to an Adapter instance")
    return adapter
