from bogi.agent import BogiAgent, build_agent
from bogi.llm import make_model


def test_make_model_uses_default_tier() -> None:
    model = make_model()

    assert model.model_name == "smart"


def test_build_agent_smoke() -> None:
    agent = build_agent()

    assert agent is not None


async def test_bogi_agent_close_smoke() -> None:
    agent = BogiAgent()

    await agent.close()
