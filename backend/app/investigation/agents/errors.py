from __future__ import annotations


class AgentOutputContractError(ValueError):
    """The model responded, but its output could not satisfy the Agent contract."""


class ModelOutputSchemaError(AgentOutputContractError):
    """The provider response was reachable but was not a usable JSON object."""
