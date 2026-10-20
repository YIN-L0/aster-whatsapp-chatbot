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
    llm_provider: str = "openai"  # openai | anthropic
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"
    anthropic_fallbacks: bool = True
    openai_model: str = "gpt-5"
    openai_reasoning_effort: str = "low"  # minimal | low | medium | high – lower = faster, cheaper replies
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Simulated patients and eval judge – a different model from the bot under test
    sim_patient_provider: str = "openai"  # openai | anthropic
    sim_patient_model: str = ""  # default: gpt-5-mini / claude-sonnet-5-5
    eval_judge_provider: str = "openai"
    eval_judge_model: str = "gpt-5-mini"

    # Clinic / demo
    clinic_timezone: str = "America/Indiana/Indianapolis"
    staff_whatsapp_numbers: str = ""  # comma separated wa_ids that receive handoff alerts
    demo_mode: bool = True
    admin_token: str = "change-me"
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
