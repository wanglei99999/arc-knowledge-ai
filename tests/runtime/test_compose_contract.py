from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def load_compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


def load_env_example() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


def test_application_services_share_one_backend_image():
    services = load_compose()["services"]

    assert services["api"]["image"] == "incipit-api:r0"
    assert services["worker"]["image"] == "incipit-api:r0"
    assert services["migrate"]["image"] == "incipit-api:r0"


def test_every_published_port_binds_loopback():
    for service in load_compose()["services"].values():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:")


def test_no_runtime_image_uses_latest():
    for service in load_compose()["services"].values():
        image = service.get("image", "")
        assert not image.endswith(":latest")


def test_optional_services_have_expected_profiles():
    services = load_compose()["services"]

    assert services["infinity"]["profiles"] == ["rerank"]
    assert services["paddleocr"]["profiles"] == ["ocr"]
    assert services["mineru"]["profiles"] == ["ocr"]
    for name in ("prometheus", "grafana", "phoenix"):
        assert services[name]["profiles"] == ["observe"]


def test_core_service_contract_is_complete():
    services = load_compose()["services"]

    assert {
        "postgres",
        "minio",
        "etcd",
        "milvus",
        "elasticsearch",
        "redis",
        "temporal",
        "temporal-ui",
        "migrate",
        "api",
        "worker",
        "web",
    } <= services.keys()


def test_runtime_images_use_exact_r0_pins():
    services = load_compose()["services"]
    expected = {
        "postgres": "postgres:16.3-alpine",
        "minio": "minio/minio:RELEASE.2024-07-16T23-46-41Z",
        "etcd": "quay.io/coreos/etcd:v3.5.14",
        "milvus": "milvusdb/milvus:v2.4.9",
        "elasticsearch": "docker.elastic.co/elasticsearch/elasticsearch:8.13.4",
        "redis": "redis/redis-stack-server:7.4.0-v5",
        "temporal": "temporalio/auto-setup:1.24.2",
        "temporal-ui": "temporalio/ui:2.26.2",
        "infinity": "michaelf34/infinity:0.0.77-cpu",
        "prometheus": "prom/prometheus:v2.52.0",
        "grafana": "grafana/grafana:10.4.2",
        "phoenix": "arizephoenix/phoenix:version-18.0.0",
    }

    assert {name: services[name]["image"] for name in expected} == expected


def test_application_build_contexts_and_commands_are_wired():
    services = load_compose()["services"]

    assert services["migrate"]["build"]["context"] == "."
    assert services["migrate"]["command"] == ["python", "scripts/runtime/bootstrap.py"]
    assert services["api"]["build"]["context"] == "."
    assert services["worker"]["command"] == ["python", "scripts/start_worker.py"]
    assert services["web"]["build"]["context"] == "../arc-knowledge-web"
    assert services["web"]["build"]["args"]["VITE_API_BASE_URL"] == "/api"


def test_application_environment_maps_public_model_contract_to_backend_settings():
    environment = load_compose()["x-app-environment"]

    assert environment["OPENAI_BASE_URL"] == environment["LLM_BASE_URL"]
    assert environment["OPENAI_API_KEY"] == environment["LLM_API_KEY"]
    assert environment["OPENAI_LLM_MODEL"] == environment["LLM_MODEL"]
    assert environment["OPENAI_EMBEDDING_BASE_URL"] == environment["EMBEDDING_BASE_URL"]
    assert environment["OPENAI_EMBEDDING_API_KEY"] == environment["EMBEDDING_API_KEY"]
    assert environment["OPENAI_EMBEDDING_MODEL"] == environment["EMBEDDING_MODEL"]
    assert environment["JWT_SECRET"] == environment["JWT_SECRET_KEY"]
    assert environment["OTLP_ENDPOINT"] == environment["OTEL_EXPORTER_OTLP_ENDPOINT"]


def test_optional_model_services_are_offline_and_infinity_models_are_read_only():
    services = load_compose()["services"]

    for name in ("infinity", "paddleocr", "mineru"):
        assert services[name]["environment"]["HF_HUB_OFFLINE"] == "${HF_HUB_OFFLINE:-1}"
        assert services[name]["environment"]["TRANSFORMERS_OFFLINE"] == (
            "${TRANSFORMERS_OFFLINE:-1}"
        )
    assert "./models:/models:ro" in services["infinity"]["volumes"]


def test_milvus_metrics_port_stays_inside_compose_network():
    ports = load_compose()["services"]["milvus"]["ports"]

    assert all(not str(port).endswith(":9091") for port in ports)


def test_named_volumes_cover_persistent_services():
    volumes = load_compose()["volumes"]

    assert {
        "postgres_data",
        "minio_data",
        "milvus_data",
        "etcd_data",
        "redis_data",
        "es_data",
        "prometheus_data",
        "grafana_data",
        "paddleocr_models",
        "mineru_models",
        "phoenix_data",
    } <= volumes.keys()


def test_env_example_exposes_all_host_and_model_overrides_without_real_keys():
    values = load_env_example()

    assert {
        "COMPOSE_PROJECT_NAME",
        "POSTGRES_HOST_PORT",
        "MINIO_API_HOST_PORT",
        "MINIO_CONSOLE_HOST_PORT",
        "MILVUS_HOST_PORT",
        "ELASTICSEARCH_HOST_PORT",
        "REDIS_HOST_PORT",
        "TEMPORAL_HOST_PORT",
        "TEMPORAL_UI_HOST_PORT",
        "API_HOST_PORT",
        "WEB_HOST_PORT",
        "INFINITY_HOST_PORT",
        "PADDLEOCR_HOST_PORT",
        "MINERU_HOST_PORT",
        "PROMETHEUS_HOST_PORT",
        "GRAFANA_HOST_PORT",
        "PHOENIX_HOST_PORT",
        "PHOENIX_OTLP_HOST_PORT",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_DIMENSIONS",
        "INCIPIT_OPTIONAL_SERVICES",
    } <= values.keys()
    assert "sk-" not in (ROOT / ".env.example").read_text(encoding="utf-8")
