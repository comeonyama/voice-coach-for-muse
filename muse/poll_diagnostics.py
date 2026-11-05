"""Bounded poll failure diagnostics; never log request URLs or credentials."""
import json
import re
from urllib.parse import quote


def sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {str(k): ('[REDACTED]' if any(x in str(k).lower() for x in
                ('token', 'secret', 'authorization', 'cookie', 'url')) else sanitize(v, secrets))
                for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v, secrets) for v in value[:30]]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, '[REDACTED]').replace(quote(secret, safe=''), '[REDACTED]')
        value = re.sub(r'https?://[^\s\"\'<>]+', '[URL REDACTED]', value)
        value = re.sub(r'(?i)(token|secret|authorization|cookie)([\s:=]+)[^\s,;]+', r'\1\2[REDACTED]', value)
        return value[:2000]
    return value


def summary(value, secrets=()):
    return json.dumps(sanitize(value, secrets), ensure_ascii=False)[:6000]
