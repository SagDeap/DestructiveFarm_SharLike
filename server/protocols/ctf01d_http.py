import requests

from server.models import FlagStatus, SubmitResult


TIMEOUT = 5


def submit_flags(flags, config):
    for item in flags:
        r = requests.get(config['SYSTEM_URL'], params={
            'teamid': config['TEAM_ID'],
            'flag': item.flag,
        }, timeout=TIMEOUT)

        response = r.text.strip()
        if r.status_code == 200:
            status = FlagStatus.ACCEPTED
        elif r.status_code == 403:
            status = FlagStatus.REJECTED
        else:
            status = FlagStatus.QUEUED
            response = '{} {}'.format(r.status_code, response)

        yield SubmitResult(item.flag, status, response)
