import threading
import time
from collections import Counter, deque

from server.models import FlagStatus


RECENT_LIMIT = 40

_lock = threading.RLock()
_state = {
    'loop_started_at': None,
    'last_tick_at': None,
    'last_submit_started_at': None,
    'last_submit_finished_at': None,
    'last_submit_duration': None,
    'last_total_queued': 0,
    'last_submitted_count': 0,
    'last_result_counts': {},
    'last_error': None,
    'last_success_at': None,
    'consecutive_error_rounds': 0,
    'total_submitted': 0,
    'total_results': Counter(),
    'recent_responses': deque(maxlen=RECENT_LIMIT),
}


def _short_flag(flag):
    if len(flag) <= 18:
        return flag
    return '{}...{}'.format(flag[:12], flag[-6:])


def mark_loop_started():
    now = time.time()
    with _lock:
        _state['loop_started_at'] = now
        _state['last_tick_at'] = now


def mark_no_flags(total_queued, duration):
    now = time.time()
    with _lock:
        _state['last_tick_at'] = now
        _state['last_submit_started_at'] = None
        _state['last_submit_finished_at'] = None
        _state['last_submit_duration'] = duration
        _state['last_total_queued'] = total_queued
        _state['last_submitted_count'] = 0
        _state['last_result_counts'] = {}
        _state['last_error'] = None


def record_submit(started_at, total_queued, flags, results, duration):
    now = time.time()
    result_counts = Counter(item.status.name for item in results)
    flag_meta = {item.flag: item for item in flags}
    definitive_count = result_counts[FlagStatus.ACCEPTED.name] + result_counts[FlagStatus.REJECTED.name]
    error_round = len(results) > 0 and definitive_count == 0 and result_counts[FlagStatus.QUEUED.name] > 0

    with _lock:
        _state['last_tick_at'] = now
        _state['last_submit_started_at'] = started_at
        _state['last_submit_finished_at'] = now
        _state['last_submit_duration'] = duration
        _state['last_total_queued'] = total_queued
        _state['last_submitted_count'] = len(flags)
        _state['last_result_counts'] = dict(result_counts)
        _state['last_error'] = None
        _state['total_submitted'] += len(results)
        _state['total_results'].update(result_counts)

        if definitive_count > 0:
            _state['last_success_at'] = now
            _state['consecutive_error_rounds'] = 0
        elif error_round:
            _state['consecutive_error_rounds'] += 1

        for item in results:
            source = flag_meta.get(item.flag)
            _state['recent_responses'].appendleft({
                'time': now,
                'flag': _short_flag(item.flag),
                'sploit': source.sploit if source else '',
                'team': source.team if source else '',
                'status': item.status.name,
                'response': item.checksystem_response or '',
            })


def record_submit_exception(started_at, total_queued, flags, error, duration):
    now = time.time()
    with _lock:
        _state['last_tick_at'] = now
        _state['last_submit_started_at'] = started_at
        _state['last_submit_finished_at'] = now
        _state['last_submit_duration'] = duration
        _state['last_total_queued'] = total_queued
        _state['last_submitted_count'] = len(flags)
        _state['last_result_counts'] = {FlagStatus.QUEUED.name: len(flags)}
        _state['last_error'] = '{}: {}'.format(type(error).__name__, error)
        _state['consecutive_error_rounds'] += 1


def snapshot():
    with _lock:
        result = dict(_state)
        result['total_results'] = dict(_state['total_results'])
        result['recent_responses'] = list(_state['recent_responses'])
        return result
