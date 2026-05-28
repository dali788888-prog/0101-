from __future__ import annotations

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_host: str = '0.0.0.0'
    app_port: int = 8099
    app_name: str = 'Hermes Agent Auto Executor'
    log_level: str = 'INFO'
    hermes_agent_api_key: str = ''

    ollama_base_url: str = 'http://host.docker.internal:11434'
    ollama_model: str = 'hermes3:8b'
    ollama_timeout_seconds: int = 180
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096

    search_provider: str = 'none'
    max_search_results: int = 8
    brave_search_api_key: str = ''
    tavily_api_key: str = ''
    serpapi_api_key: str = ''
    searxng_url: str = 'http://searxng:8080/search'

    http_timeout_seconds: int = 20
    user_agent: str = 'HermesAgentResearchBot/1.0'

    database_url: str = 'sqlite:////app/storage/hermes_agent.db'
    report_dir: str = '/app/storage/reports'

    telegram_bot_token: str = ''
    telegram_chat_id: str = ''
    webhook_url: str = ''

    live_gate_enabled: bool = False
    live_gate_max_order_usdt: str = '20'
    live_gate_daily_limit_usdt: str = '50'


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
