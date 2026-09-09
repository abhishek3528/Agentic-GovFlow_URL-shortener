"""Capability-owned task dispatch for agentic scenario execution.

An :class:`AgentRegistry` is a second implementation of the narrow
``TaskExecutor`` seam.  Dispatch is deliberately two-level: a task's frozen
``capability`` selects its owning agent, then the task id selects work from that
agent's handler table.  Missing ownership or work fails closed so capability is
an executable contract rather than evidence-only metadata.
"""

from __future__ import annotations

from dataclasses import dataclass

from orchestrator.contracts import Actor, ActorKind, Task
from orchestrator.executor import Handler, TaskOutput


class AgentRegistryError(RuntimeError):
    """Base error for invalid registration or dispatch."""


class AgentDispatchError(AgentRegistryError):
    """A task could not be routed to an agent-owned handler."""


@dataclass
class Agent:
    """A named role that performs the work of tasks declaring its capability."""

    name: str
    capability: str
    handlers: dict[str, Handler]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("agent name must not be empty")
        if not self.capability.strip():
            raise ValueError("agent capability must not be empty")
        invalid = sorted(
            task_id
            for task_id, handler in self.handlers.items()
            if not task_id.strip() or not callable(handler)
        )
        if invalid:
            raise ValueError(
                "agent handlers require non-empty task ids and callable work: "
                + ", ".join(invalid)
            )


class AgentRegistry:
    """Route tasks through their declared capability and owning agent."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """Register one exclusive owner for a capability."""
        if agent.capability in self._agents:
            raise AgentRegistryError(
                f"capability '{agent.capability}' already has a registered agent"
            )
        self._agents[agent.capability] = agent

    def agent_for(self, task: Task) -> Agent:
        """Resolve the role owner for ``task``, failing closed when absent."""
        agent = self._agents.get(task.capability)
        if agent is None:
            raise AgentDispatchError(
                f"no agent registered for capability '{task.capability}' "
                f"required by task '{task.id}'"
            )
        return agent

    def actor_for(self, task: Task) -> Actor:
        """Return the audit identity of the agent that owns ``task``."""
        agent = self.agent_for(task)
        return Actor(kind=ActorKind.AGENT, id=agent.name)

    def execute(self, task: Task, inputs: dict[str, str]) -> TaskOutput:
        """Perform two-level capability -> task-id dispatch."""
        agent = self.agent_for(task)
        handler = agent.handlers.get(task.id)
        if handler is None:
            raise AgentDispatchError(
                f"agent '{agent.name}' has no handler for task '{task.id}'"
            )
        return handler(task, inputs)


__all__ = [
    "Agent",
    "AgentDispatchError",
    "AgentRegistry",
    "AgentRegistryError",
]
