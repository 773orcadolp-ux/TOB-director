import json
import os
import sys
import urllib.request


def post_to_slack(webhook_env_name, text):
    """指定された環境変数名のWebhookに投稿"""
    webhook = os.environ.get(webhook_env_name, '')
    if not webhook:
        print(f'{webhook_env_name} not set', file=sys.stderr)
        return False
    payload = {'text': text}
    req = urllib.request.Request(
        webhook,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            print(f'Slack notified ({webhook_env_name}): {res.status}')
            return True
    except Exception as e:
        print(f'Slack notification failed ({webhook_env_name}): {e}', file=sys.stderr)
        return False
