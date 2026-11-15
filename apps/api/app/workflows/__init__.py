"""Application-level operations that span modules (ADR-0002; architecture
§4.2): each runs in the caller's one Unit of Work. Modules never import
this package; the composition root (``app.wiring``) wires its steps in."""
