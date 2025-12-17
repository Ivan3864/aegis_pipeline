# keycloak_config.py

KEYCLOAK_SERVER_URL = "http://localhost:8080"
KEYCLOAK_REALM = "AEGIS"

# OpenID Connect discovery document
OIDC_CONFIG_URL = (
    f"{KEYCLOAK_SERVER_URL}/realms/{KEYCLOAK_REALM}/.well-known/openid-configuration"
)

# This must match your Keycloak client ID
KEYCLOAK_AUDIENCE = "aegis-dashboard"
