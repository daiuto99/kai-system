"""SSOT for privacy-tier advisor membership (P5 af5245bd).

Private advisors whose turns must never leave the local privacy boundary.
Was hand-duplicated across kai-council-api + kai-worker-api (drift risk); this
is now the single authored source. Both services mount /shared on sys.path."""

PRIVACY_ADVISORS = frozenset({"ember", "doc"})
