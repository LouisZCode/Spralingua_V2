"""ChatOpenAI subclass that preserves OpenRouter's top-level ``provider`` field.

langchain_openai copies only an allowlist of response fields into
``response_metadata`` and silently drops OpenRouter's ``provider`` extension
(the actually-served upstream, e.g. "Cerebras") — but the openai SDK keeps
it in ``ChatCompletion.model_extra``. Rescuing it here lets every judge span
record which provider actually served the call — the whole point of the
Cerebras-pin diagnostics (OBS-008): a call that silently fell off Cerebras
goes from ~2s to 30-70s, and without this field the served provider was
recorded nowhere.

Installed langchain_openai's ``_create_chat_result`` signature (verified in
``.venv/.../langchain_openai/chat_models/base.py``)::

    def _create_chat_result(
        self,
        response: dict | openai.BaseModel,
        generation_info: dict | None = None,
    ) -> ChatResult

Both the plain ``ainvoke`` path and the structured-output beta-parse path
route through it. After it returns, langchain_core reassigns
``message.response_metadata = {**generation_info, **message.response_metadata}``
and then ``{**llm_output, **message.response_metadata}`` — the message's own
dict WINS both merges, so the key stamped here survives onto the final
AIMessage that ``unwrap_structured_output`` sees.

NOT used by ``agents/conversation_agent.py``: the live voice agent streams,
and streamed chunks are assembled by ``_astream``/``_convert_chunk_to_generation_chunk``
— a different code path ``_create_chat_result`` never touches, so this
subclass would add nothing there (see the Task 3 note in
``agents/pipecat_wrapper.py``).
"""

from langchain_openai import ChatOpenAI


class ProviderChatOpenAI(ChatOpenAI):
    """ChatOpenAI that copies OpenRouter's served ``provider`` into
    ``response_metadata["openrouter_provider"]`` on every generation."""

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        provider = None
        extra = getattr(response, "model_extra", None)
        if extra:
            provider = extra.get("provider")
        elif isinstance(response, dict):
            provider = response.get("provider")
        if provider:
            for gen in result.generations:
                gen.message.response_metadata["openrouter_provider"] = provider
        return result
