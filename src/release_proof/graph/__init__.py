"""Workflow package with no eager dependency on runtime adapters."""

def __getattr__(name: str):
    if name == "ReleaseProofService":
        from release_proof.graph.service import ReleaseProofService

        return ReleaseProofService
    raise AttributeError(name)
