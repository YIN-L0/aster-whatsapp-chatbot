from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database: sqlite for local dev, Postgres on Render (postgres://... is normalised below)
    database_url: str = "sqlite+aiosqlite:///./aster_demo.db"

    # WhatsApp Cloud API
    wa_access_token: str = ""
    wa_phone_number_id: str = ""
    wa_app_secret: str = ""
    wa_verify_token: str = "aster-demo-verify"
    wa_graph_version: str = "v23.0"
    # Optional approved utility template for check-ins outside the 24h window
    wa_checkin_template: str = ""
    wa_template_lang: str = "en_US"

    # LLM
    llm_provider: str = "anthropic"  # anthropic | openai
    llm_fallback: bool = True  # if the main provider's API call fails, answer with the other one (when its key is set)
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"  # low | medium | high; empty = model default
    anthropic_speed: str = "fast"  # "fast" = fast mode (Opus only, ~2x price)
    anthropic_fallbacks: bool = True
    openai_model: str = "gpt-5"
    openai_reasoning_effort: str = "low"  # minimal | low | medium | high; empty for non-reasoning models (gpt-4.1)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Simulated patients and eval judge – a different model from the bot under test
    sim_patient_provider: str = "openai"  # openai | anthropic
    sim_patient_model: str = ""  # default: gpt-5-mini / claude-sonnet-5-5
    eval_judge_provider: str = "openai"
    eval_judge_model: str = "gpt-5-mini"

    # Clinic / demo
    clinic_timezone: str = "America/Indiana/Indianapolis"
    privacy_policy_url: str = "https://aster-us.com/#/privacy"
    # Aster booking backend (Bot API v1). Empty = demo mock slots.
    booking_api_url: str = ""
    booking_api_key: str = ""
    # DEMO: the booking backend requires a birth date; the demo assumes adults and sends this instead of asking.
    # Empty = ask the patient. Must be emptied before real patients book.
    booking_default_birth_date: str = "1990-01-01"
    staff_whatsapp_numbers: str = ""  # comma separated wa_ids that receive handoff alerts
    demo_mode: bool = True
    admin_token: str = "admin-key"
    checkin_send_window: str = "09:00-19:00"
    scheduler_interval_seconds: int = 30

    @property
    def async_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql+asyncpg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://"):]
        return url

    @property
    def staff_numbers(self) -> list[str]:
        return [n.strip().lstrip("+") for n in self.staff_whatsapp_numbers.split(",") if n.strip()]

    @property
    def whatsapp_enabled(self) -> bool:
        return bool(self.wa_access_token and self.wa_phone_number_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
