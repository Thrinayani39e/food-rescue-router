"""AgentCore Memory: gives the coordinator real short-term recall across donations
within a run -- e.g. that a driver it just assigned is no longer free, or that a food
bank it matched a load to five minutes ago is now closer to capacity -- via Bedrock
AgentCore's managed memory service, not a hand-rolled cache. Uses the real
bedrock_agentcore.memory integration (MemoryClient + AgentCoreMemorySessionManager),
not a local substitute.

The memory resource is created once and reused (by name) across restarts, and its id
is cached in-process so repeated agent builds don't re-create or re-look-it-up every
call.
"""
import logging
import time

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

logger = logging.getLogger(__name__)

MEMORY_NAME = "WindfallCoordinatorMemory"
SESSION_ID = "windfall-network-session"
ACTOR_ID = "coordinator"

_memory_id_cache: str | None = None


def _wait_until_active(client: MemoryClient, memory_id: str, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = client.get_memory_status(memory_id)
        if status == "ACTIVE":
            return
        if status in ("FAILED",):
            raise RuntimeError(f"AgentCore memory {memory_id} failed to provision (status={status})")
        time.sleep(2)
    raise TimeoutError(f"AgentCore memory {memory_id} did not become ACTIVE within {timeout_s}s")


def get_session_manager(region: str) -> AgentCoreMemorySessionManager | None:
    """Return a session manager backed by a real, ACTIVE AgentCore Memory resource, or
    None if memory can't be provisioned right now (e.g. missing IAM permission) -- the
    coordinator still works without it, just without cross-donation recall, so a memory
    outage should degrade the feature rather than take down routing entirely.
    """
    global _memory_id_cache
    client = MemoryClient(region_name=region)
    try:
        if _memory_id_cache is None:
            memory = client.create_or_get_memory(name=MEMORY_NAME, description="Windfall coordinator cross-donation recall")
            _memory_id_cache = memory["id"]
        _wait_until_active(client, _memory_id_cache)
        config = AgentCoreMemoryConfig(memory_id=_memory_id_cache, session_id=SESSION_ID, actor_id=ACTOR_ID)
        return AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=region)
    except Exception:
        logger.exception("AgentCore memory unavailable; coordinator will run without cross-donation recall.")
        return None
