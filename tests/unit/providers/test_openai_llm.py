from app.providers.llm.openai_llm import OpenAILLMProvider


def test_deepseek_v4_flash_uses_one_million_token_context_window() -> None:
    provider = OpenAILLMProvider()
    provider._default_model = "deepseek-v4-flash"

    assert provider.get_context_window() == 1_000_000
