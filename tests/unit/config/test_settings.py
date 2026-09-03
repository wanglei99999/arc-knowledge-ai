from app.config.settings import Settings


def test_settings_ignores_compose_only_fields_in_the_shared_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_ENV", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "APP_ENV=test\nCOMPOSE_PROJECT_NAME=incipit\nPOSTGRES_HOST_PORT=15432\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.app_env == "test"
