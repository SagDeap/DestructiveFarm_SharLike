from concurrent.futures import ThreadPoolExecutor

import requests

from server.models import FlagStatus, SubmitResult


TIMEOUT = 5
MAX_WORKERS = 10


def submit_one(item, config):
    try:
        r = requests.get(config['SYSTEM_URL'], params={
            'teamid': config['TEAM_ID'],
            'flag': item.flag,
        }, timeout=TIMEOUT)

        body = r.text.strip()
        if r.status_code == 200:
            status = FlagStatus.ACCEPTED
        elif r.status_code == 403:
            status = FlagStatus.REJECTED
        else:
            status = FlagStatus.QUEUED
        response = '{} {}'.format(r.status_code, body).strip()
    except Exception as e:
        status = FlagStatus.QUEUED
        response = '{}: {}'.format(type(e).__name__, str(e))

    return SubmitResult(item.flag, status, response)


def submit_flags(flags, config):
    workers = min(MAX_WORKERS, max(1, len(flags)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(lambda item: submit_one(item, config), flags)
