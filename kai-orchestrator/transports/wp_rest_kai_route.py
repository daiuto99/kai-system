from .base import safe_request, SafeResponse


def set_option(site: str, option: str, value: str, creds: dict) -> SafeResponse:
    return safe_request(
        "POST",
        f"https://{creds['fqdn']}/wp-json/kai/v1/option/{option}",
        auth=("kai", creds["app_password"]),
        json={"value": value},
        verify=False,
    )


def get_option(site: str, option: str, creds: dict) -> SafeResponse:
    return safe_request(
        "GET",
        f"https://{creds['fqdn']}/wp-json/kai/v1/option/{option}",
        auth=("kai", creds["app_password"]),
        verify=False,
    )
