"""Concord — GSTR-2B reconciliation that runs on your own machine.

Built for CA firms in India. The client's purchase register is never uploaded
anywhere: no server, no account, no telemetry. That is a professional ethics
requirement before it is a feature.
"""

from .gstr2b import load as load_gstr2b
from .match import reconcile
from .memory import load as load_memory, save as save_memory
from .register import load as load_register
from .report import full_report, to_csv

__version__ = "1.0.0"

__all__ = [
    "load_gstr2b", "load_register", "reconcile", "load_memory", "save_memory",
    "full_report", "to_csv", "__version__",
]
