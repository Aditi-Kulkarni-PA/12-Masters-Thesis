
"""
    clients.py will be used by the delivery_agents.py to create a chat client for the 
    OpenAI API or a local LM Studio endpoint, based on environment variables. 
    It also sets up a lower-cost client for lightweight tasks.
    import chat_client and chat_client_mini from this module in delivery_agents.py 
    to use the configured clients.
"""
import os
from dotenv import load_dotenv, find_dotenv

# Load environment variables from the nearest .env file, but preserve any
# values already provided by the shell/runtime.
load_dotenv(dotenv_path=find_dotenv(), override=False)

from agent_framework.openai import OpenAIChatClient

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
MODEL_MINI = os.getenv("OPENAI_MODEL_MINI", "gpt-4.1-mini")

def make_chat_client(function_invocation_configuration=None) -> OpenAIChatClient:
    """Configure provider routing for OpenAI cloud or local LM Studio.

    *function_invocation_configuration* is passed through to the client for callers
    needing to bound its internal tool-calling loop (e.g. max_function_calls). Left
    None, the framework defaults apply.
    """
    backend = os.getenv("LLM_BACKEND", "openai").strip().lower()

    # Use a local OpenAI-compatible endpoint for offline/local development.
    if backend == "lmstudio":
        return OpenAIChatClient(
            model=MODEL,
            base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1").strip(),
            api_key = os.getenv("LLM_API_KEY", "lm-studio").strip() or "lm-studio",
            function_invocation_configuration=function_invocation_configuration,
        )
    return OpenAIChatClient(  # default: OpenAI cloud
        model=MODEL,
        function_invocation_configuration=function_invocation_configuration,
    )

# Primary client used across core agent workflows.
chat_client = make_chat_client()
# Lower-cost client for lightweight utility/formatting tasks.
chat_client_mini = OpenAIChatClient(model=MODEL_MINI)  # for lightweight formatting agent

