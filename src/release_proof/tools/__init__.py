from release_proof.tools.policy import ToolPolicy as ToolPolicy
from release_proof.tools.policy import ToolPolicyError as ToolPolicyError


def __getattr__(name: str):
    if name in {"ReadOnlyToolRegistry", "ToolCall", "ToolObservation"}:
        from release_proof.tools.registry import ReadOnlyToolRegistry, ToolCall, ToolObservation

        return {
            "ReadOnlyToolRegistry": ReadOnlyToolRegistry,
            "ToolCall": ToolCall,
            "ToolObservation": ToolObservation,
        }[name]
    raise AttributeError(name)
