import os
from typing import List

try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    HAS_KEYVAULT = True
except ImportError:
    HAS_KEYVAULT = False

# Map Key Vault secret names (kebab-case) -> runtime environment variables
KEYVAULT_SECRET_MAP = {
    "AZURE-OPENAI-ENDPOINT": "AZURE_OPENAI_ENDPOINT",
    "AZURE-OPENAI-API-KEY": "AZURE_OPENAI_API_KEY",
    "AZURE-OPENAI-DEPLOYMENT-NAME": "AZURE_OPENAI_DEPLOYMENT_NAME",
    "AZURE-OPENAI-API-VERSION": "AZURE_OPENAI_API_VERSION",
    "AZURE-AI-FOUNDRY-PROJECT-ENDPOINT": "AZURE_AI_FOUNDRY_PROJECT_ENDPOINT",
    "AZURE-AI-FOUNDRY-SERVICES-ENDPOINT": "AZURE_AI_FOUNDRY_SERVICES_ENDPOINT",
    "AZURE-SEARCH-ENDPOINT": "AZURE_SEARCH_ENDPOINT",
    "AZURE-SEARCH-KEY": "AZURE_SEARCH_KEY",
    "AZURE-SEARCH-INDEX-NAME": "AZURE_SEARCH_INDEX_NAME",
    "APPLICATIONINSIGHTS-CONNECTION-STRING": "APPLICATIONINSIGHTS_CONNECTION_STRING",
    "APPLICATIONINSIGHTS-INSTRUMENTATION-KEY": "APPLICATIONINSIGHTS_INSTRUMENTATION_KEY",
    "AZURE-CONTENT-SAFETY-ENDPOINT": "AZURE_CONTENT_SAFETY_ENDPOINT",
    "AZURE-CONTENT-SAFETY-KEY": "AZURE_CONTENT_SAFETY_KEY",
    "AZURE-BOT-NAME": "AZURE_BOT_NAME",
    "AZURE-BOT-APP-ID": "AZURE_BOT_APP_ID",
    "AZURE-BOT-DIRECTLINE-KEY": "AZURE_BOT_DIRECTLINE_KEY",
    "POWER-AUTOMATE-WEBHOOK-URL": "POWER_AUTOMATE_WEBHOOK_URL",
}


def load_azure_secrets_from_keyvault(vault_url: str = "") -> List[str]:
    """Fetch Azure service secrets from Azure Key Vault into os.environ."""
    endpoint = vault_url or os.getenv("AZURE_KEY_VAULT_ENDPOINT", "https://complaint-kv-mridul.vault.azure.net/")
    if not HAS_KEYVAULT or not endpoint:
        return []

    loaded: List[str] = []
    try:
        client = SecretClient(vault_url=endpoint, credential=DefaultAzureCredential())
        for secret_name, env_var in KEYVAULT_SECRET_MAP.items():
            try:
                secret = client.get_secret(secret_name)
                if secret and secret.value:
                    os.environ[env_var] = secret.value
                    loaded.append(env_var)
            except Exception:
                continue
    except Exception:
        return []

    return loaded
