"""Shared Bedrock model config for the coordinator and its specialist sub-agents."""
import os

from strands.models import BedrockModel


def build_model(temperature: float = 0.2) -> BedrockModel:
    return BedrockModel(
        model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        temperature=temperature,
    )
