from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

import httpx
from openai import AsyncOpenAI

from agentforce_simulator.config import AppConfig
from agentforce_simulator.schemas import ConversationTurn


class LLMClient(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: Iterable[ConversationTurn],
    ) -> str:
        raise NotImplementedError


class HeuristicLLMClient(LLMClient):
    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: Iterable[ConversationTurn],
    ) -> str:
        recent_context = " ".join(turn.content for turn in list(history)[-2:])
        guidance = system_prompt.split(".")[0].strip()
        if recent_context:
            return f"{guidance}. Based on the recent context, here is the best next step: {user_prompt}"
        return f"{guidance}. Here is the recommended response: {user_prompt}"


class OpenAILLMClient(LLMClient):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: Iterable[ConversationTurn],
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        for turn in history:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": user_prompt})
        response = await self._client.chat.completions.create(model=self._model, messages=messages)
        content = response.choices[0].message.content or ""
        return content.strip()


class OllamaLLMClient(LLMClient):
    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: Iterable[ConversationTurn],
    ) -> str:
        conversation = [f"System: {system_prompt}"]
        for turn in history:
            conversation.append(f"{turn.role.title()}: {turn.content}")
        conversation.append(f"User: {user_prompt}")
        prompt = "\n".join(conversation)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self._base_url}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
            return str(payload.get("response", "")).strip()


class ResilientLLMClient(LLMClient):
    def __init__(self, primary: LLMClient, fallback: LLMClient | None = None) -> None:
        self._primary = primary
        self._fallback = fallback or HeuristicLLMClient()

    async def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        history: Iterable[ConversationTurn],
    ) -> str:
        try:
            return await self._primary.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=history,
            )
        except Exception:
            return await self._fallback.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                history=history,
            )


def build_llm_client(config: AppConfig) -> LLMClient:
    provider = config.llm_provider
    if provider == "openai" and config.openai_api_key:
        return ResilientLLMClient(OpenAILLMClient(config.openai_api_key, config.openai_model))
    if provider == "ollama":
        return ResilientLLMClient(OllamaLLMClient(config.ollama_base_url, config.ollama_model))
    return HeuristicLLMClient()
