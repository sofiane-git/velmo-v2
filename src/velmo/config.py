"""Configuration centralisée : une seule source pour les noms de variables
d'environnement et leurs valeurs par défaut (au lieu d'`os.getenv` éparpillés
dans chaque module).

Lit uniquement `os.environ` (pas de chargement `.env` ici) : ce module suppose
que l'environnement est déjà peuplé en amont — par `load_dotenv()` en CLI, par
`env_file:` de Docker Compose en conteneur — sinon dupliquer cette lecture
casserait l'isolation des tests, qui neutralisent des variables via
`monkeypatch.delenv`.

`get_settings()` construit une instance fraîche à chaque appel (pas de
singleton mis en cache) : les tests qui font `monkeypatch.setenv`/`delenv`
doivent voir l'effet au prochain appel, comme avec `os.getenv`.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def require(value: str | None, env_name: str) -> str:
    """Valide qu'une variable de config optionnelle est bien présente à ce
    point d'usage — lève `KeyError` avec le nom de la variable d'environnement,
    même contrat que l'`os.environ[...]` qu'elle remplace."""
    if not value:
        raise KeyError(env_name)
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    # Base de données (Postgres en prod, SQLite en repli si injoignable).
    db_url: str = "postgresql+psycopg://app:app@localhost:5432/velmo"

    # Agent principal : Azure AI Inference (Mistral-Large-3).
    azure_ai_inference_endpoint: str | None = None
    azure_ai_inference_api_key: str | None = None
    azure_ai_inference_model: str = "Mistral-Large-3"

    # Juge garde-fous : Azure OpenAI (gpt-5-mini).
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_deployment: str = "gpt-5-mini"

    # Classifieur de modération : Llama Guard 3 servi via Ollama.
    ollama_url: str | None = None
    llama_guard_model: str = "llama-guard3:8b"

    # PII redaction en texte libre : Azure AI Language.
    azure_language_endpoint: str | None = None
    azure_language_key: str | None = None

    # Prompt Shields : Azure AI Content Safety.
    azure_content_safety_endpoint: str | None = None
    azure_content_safety_key: str | None = None

    # Mémoire épisodique + KB vectorielle : Chroma.
    chroma_url: str | None = None
    embedding_model: str = "intfloat/multilingual-e5-small"

    # API.
    velmo_web_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Distingue prod (fail-fast si config LLM absente) de dev/CI (repli EchoLLM toléré).
    environment: str = "development"

    # Seuil d'écriture mémoire long terme (§Calibration, Chantier 1) — point de
    # départ à calibrer sur `eval/memory_confidence_cases.jsonl`, pas une
    # constante gravée : voir aussi le seuil équivalent des garde-fous (Ch.2).
    memory_confidence_threshold: float = 0.7


def get_settings() -> Settings:
    return Settings()


class ConfigurationError(RuntimeError):
    """Config incohérente détectée au démarrage — distinct du `KeyError` de
    `require()` (absence simple d'une variable optionnelle à l'usage)."""


# (champ endpoint, champ clé) pour chaque intégration où les deux vont
# nécessairement ensemble. Le nom de variable d'environnement se déduit du nom
# de champ (`.upper()` — convention pydantic-settings par défaut, sans alias) :
# pas de préfixe à part à maintenir en synchro, qui diffère selon les
# intégrations (`_api_key` vs `_key`).
_ENDPOINT_KEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("azure_ai_inference_endpoint", "azure_ai_inference_api_key"),
    ("azure_openai_endpoint", "azure_openai_api_key"),
    ("azure_language_endpoint", "azure_language_key"),
    ("azure_content_safety_endpoint", "azure_content_safety_key"),
)


def validate_startup(settings: Settings | None = None) -> None:
    """Échoue tôt (au démarrage du process) plutôt qu'au premier appel qui
    découvre la config manquante.

    Chaque intégration Azure reste entièrement optionnelle par conception (les
    deux variables absentes déclenchent un repli gracieux — `EchoLLM`,
    `RuleBasedJudge`, etc.) : ce n'est donc pas une absence complète qui est une
    erreur, mais un couple endpoint/clé à moitié renseigné (typo de nom de
    variable, oubli au déploiement) — un état que le repli ne peut pas
    distinguer d'une simple non-configuration.
    """
    settings = settings or get_settings()
    errors = []
    for endpoint_field, key_field in _ENDPOINT_KEY_FIELDS:
        endpoint = getattr(settings, endpoint_field)
        key = getattr(settings, key_field)
        if bool(endpoint) != bool(key):
            missing_field = key_field if endpoint else endpoint_field
            errors.append(f"`{missing_field.upper()}` manquant (l'autre variable du couple est définie)")
    if errors:
        raise ConfigurationError(
            "Configuration incohérente :\n" + "\n".join(f"- {e}" for e in errors)
        )
