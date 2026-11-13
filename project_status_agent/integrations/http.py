"""Small JSON transport with bounded retries and sanitized failures."""

import json
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ServiceError(RuntimeError):
    pass


def request_json(
    url, headers, payload=None, *, retry=True, opener=urlopen, sleep=time.sleep
):
    attempts = 3 if retry else 1
    data = None if payload is None else json.dumps(payload).encode()
    for attempt in range(attempts):
        req = Request(
            url, data=data, headers={"Content-Type": "application/json", **headers}
        )
        try:
            with opener(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            code = exc.code
            retry_after = exc.headers.get("Retry-After", "") if exc.headers else ""
            exc.close()
            if code in (401, 403, 404):
                raise ServiceError(
                    f"HTTP {code}: check credentials, resource IDs, and connection access."
                ) from None
            if retry and code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                delay = (
                    float(retry_after)
                    if retry_after.replace(".", "", 1).isdigit()
                    else 2**attempt
                )
                sleep(min(delay, 10))
                continue
            raise ServiceError(
                f"HTTP {code}: request failed; no credentials or response bodies logged."
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError):
                raise ServiceError(
                    "Python could not verify the HTTPS certificate. On python.org macOS installations, run the supplied Install Certificates.command, then retry. Keep certificate verification enabled."
                ) from None
            if retry and attempt < attempts - 1:
                sleep(2**attempt)
                continue
            raise ServiceError(
                "Connection failed or timed out. Check network access; retry later."
            ) from None
        except (ValueError, TypeError):
            raise ServiceError("Service returned invalid JSON.") from None
