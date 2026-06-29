"""dmtemplate — declarative data-migration validation harness.

LAYER 1 (this package): golden, tested assertion code. The execution agent never
hand-writes assertion / canonicalization / dialect / hashing logic — it emits a
Migration Validation Spec (MVS, see lib/mvs.py); the harness (lib/harness.py)
routes each suite to a pattern module here. See SPEC §4.1.
"""

__version__ = "0.1.0"
