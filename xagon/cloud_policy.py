"""RC lockout for legacy external LLM transports, even with old opt-in flags.

Injected fixtures must explicitly identify themselves as offline test doubles.
This is an application guard, not a sandbox for untrusted Python code.
"""


def is_offline_client(client):
    return (client is not None
            and getattr(client, '_xagon_offline_test_client', False) is True
            and not type(client).__module__.startswith(('google.', 'openai.')))


def require_live_api(client=None):
    if is_offline_client(client):
        return
    raise PermissionError(
        'LIVE API LOCKED in ExaCortex1.0 RC: cloud execution is disabled. '
        'Legacy opt-in flags do not enable it; no request sent.')
