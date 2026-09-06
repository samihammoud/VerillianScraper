"""Service-account credentials for this project's own GCS calls.

Used by video_storage.py to read/write/copy video objects. NOT used for Gemini:
registration authenticates with the Gemini api_key alone (see vision.py's
_register_one), because every OAuth identity is refused by that endpoint --
a Bearer token alongside the api_key trips OVERLOADED_CREDENTIALS, user ADC
can't be re-scoped so it fails ACCESS_TOKEN_SCOPE_INSUFFICIENT, and a service
account is rejected by name ("Access to Gemini API is restricted with service
accounts. Use authorization keys instead"). The api_key already IS an
authorization key bound to a Google-side AI Studio service account, and it is
*that* account -- not this one -- that needs objectViewer on the bucket.

So the two identities are deliberately separate: this service account owns the
bucket's contents, the api_key's bound account only reads from it.

Loaded from the configured path rather than google.auth.default(), because
pydantic-settings reads GOOGLE_APPLICATION_CREDENTIALS out of .env into the
Settings object without exporting it to os.environ -- default() would not see
it and would silently fall back to whatever gcloud ADC the machine has.
"""

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

from src.config.settings import settings

SCOPES = [
    "https://www.googleapis.com/auth/devstorage.read_only",
    "https://www.googleapis.com/auth/cloud-platform",
]

gcp_credentials = Credentials.from_service_account_file(
    settings.google_application_credentials, scopes=SCOPES
)


def _self_check() -> None:
    assert gcp_credentials.service_account_email, "credentials loaded with no service account email"
    assert set(gcp_credentials.scopes or []) >= set(SCOPES), "credentials missing required scopes"
    gcp_credentials.refresh(Request())  # live call: confirms the key file, project, and API are actually wired up
    assert gcp_credentials.token, "refresh succeeded but no token was issued"
    print(f"gcp_auth self-check ok — service_account={gcp_credentials.service_account_email}")


if __name__ == "__main__":
    _self_check()
