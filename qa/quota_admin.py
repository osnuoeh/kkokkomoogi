"""Explicit local provisioning; never imported by app.py or invoked automatically."""
import os
import sys
from .quota import RedisQuotaStore


def main():
    if sys.argv[1:] != ['init']:
        print('Usage: python -m qa.quota_admin init')
        return 2
    try:
        store = RedisQuotaStore(os.environ['PUBLIC_AI_REDIS_URL'], os.environ['PUBLIC_AI_REDIS_TOKEN'],
                                os.environ.get('PUBLIC_AI_QUOTA_NAMESPACE', 'kkokkomoo-public-v1'))
        created = store.initialize()
        print('Quota ledger initialized.' if created else 'Ledger already exists; no counters changed.')
        return 0
    except Exception:
        print('Quota initialization failed. Check server configuration and Redis connectivity; no credentials are displayed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
