from pydantic_settings import BaseSettings


def parse_allowed_origins(value: str) -> list[str]:
    """Parse a comma-separated CORS allowlist and reject unsafe wildcards."""
    origins = [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("CORS_ALLOWED_ORIGINS must not contain '*' when credentials are enabled")
    return origins

class Settings(BaseSettings):
    APP_VERSION: str = "0.1.9"
    DATABASE_URL: str
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    RESERVED_HOST_MEMORY_GB: int = 4
    LIBVIRT_DEFAULT_URI: str = "qemu:///system"
    CONFIG_YAML_PATH: str = "/etc/personal-kvm/config.yaml"
    FRONTEND_DIST_DIR: str = "/opt/personal-kvm/frontend/dist"
    CORS_ALLOWED_ORIGINS: str = ""
    QEMU_EMULATOR_PATH: str = ""

    @property
    def cors_allowed_origins(self) -> list[str]:
        return parse_allowed_origins(self.CORS_ALLOWED_ORIGINS)

    model_config = {
        "env_file": "/etc/personal-kvm/.env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

settings = Settings()
