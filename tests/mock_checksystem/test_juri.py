#!/usr/bin/env python3
"""Mock ctf01d-совместимое жюри для локальных прогонов фермы.

Принимает GET /flag?teamid=<id>&flag=<flag>:
    200 + "+N очков"   — флаг подходит под формат и встречается впервые
    403 + причина      — пустой teamid / битый формат / уже сдан / своя команда
    500 + причина      — редкая симуляция ошибки (--error-rate)

Запуск:
    ./test_juri.py
    ./test_juri.py --port 8080 --points 1-10 --error-rate 0.05

В config.py фермы:
    'SYSTEM_PROTOCOL': 'ctf01d_http',
    'SYSTEM_URL':      'http://127.0.0.1:8080/flag',
    'TEAM_ID':         't1',
"""

import argparse
import logging
import random
import re
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


DEFAULT_FLAG_FORMAT = (
    r'^c01d[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}[0-9]{8}$'
)


class Jury:
    def __init__(self, flag_re, points_range, error_rate, own_team):
        self.flag_re = flag_re
        self.points_min, self.points_max = points_range
        self.error_rate = error_rate
        self.own_team = own_team
        self.lock = threading.Lock()
        self.accepted = {}  # flag -> (team_id, points)
        self.stats = defaultdict(
            lambda: {'accepted': 0, 'rejected': 0, 'points': 0})

    def submit(self, team_id, flag):
        if self.error_rate > 0 and random.random() < self.error_rate:
            return 500, 'queue is full, try again later'

        if not team_id:
            return 403, 'teamid is required'
        if not flag:
            return 403, 'flag is required'
        if self.own_team and team_id == self.own_team:
            return 403, "can't submit your own flag"
        if not self.flag_re.fullmatch(flag):
            with self.lock:
                self.stats[team_id]['rejected'] += 1
            return 403, 'invalid flag format'

        with self.lock:
            if flag in self.accepted:
                self.stats[team_id]['rejected'] += 1
                owner = self.accepted[flag][0]
                hint = ' (already taken by {})'.format(owner) if owner != team_id else ''
                return 403, 'flag already submitted' + hint

            points = random.randint(self.points_min, self.points_max)
            self.accepted[flag] = (team_id, points)
            self.stats[team_id]['accepted'] += 1
            self.stats[team_id]['points'] += points
            return 200, '+{} points'.format(points)

    def snapshot(self):
        with self.lock:
            return dict(self.stats), len(self.accepted)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == '/flag':
            team_id = (query.get('teamid') or [''])[0]
            flag = (query.get('flag') or [''])[0]
            code, body = self.server.jury.submit(team_id, flag)
            self._respond(code, body)
        elif parsed.path == '/stats':
            stats, total = self.server.jury.snapshot()
            lines = ['accepted total: {}'.format(total), '']
            for team, info in sorted(stats.items()):
                lines.append(
                    '{:>12} | accepted={accepted:<5} rejected={rejected:<5} points={points}'
                    .format(team, **info))
            self._respond(200, '\n'.join(lines) + '\n')
        else:
            self._respond(404, 'unknown endpoint: ' + parsed.path)

    def _respond(self, code, body):
        payload = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        logging.info('%s %s', self.address_string(), fmt % args)


def parse_points(value):
    try:
        lo, hi = (int(x) for x in value.split('-', 1))
    except ValueError:
        raise argparse.ArgumentTypeError('ожидался диапазон "MIN-MAX", напр. 1-10')
    if lo < 1 or hi < lo:
        raise argparse.ArgumentTypeError('1 <= MIN <= MAX')
    return lo, hi


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=8080)
    p.add_argument('--flag-format', default=DEFAULT_FLAG_FORMAT,
                   help='Регулярка для проверки формата флага')
    p.add_argument('--points', type=parse_points, default=(1, 10),
                   help='Диапазон очков за принятый флаг, напр. 1-10 (по умолчанию)')
    p.add_argument('--error-rate', type=float, default=0.0,
                   help='Вероятность 500-ответа [0..1] для теста ретраев')
    p.add_argument('--own-team', default='',
                   help='Если задан, отказывать этому teamid (имитация "флаг своей команды")')
    return p.parse_args()


def main():
    args = parse_args()
    if not 0.0 <= args.error_rate <= 1.0:
        raise SystemExit('--error-rate должен быть в [0, 1]')

    try:
        flag_re = re.compile(args.flag_format)
    except re.error as e:
        raise SystemExit('--flag-format: {}'.format(e))

    jury = Jury(flag_re, args.points, args.error_rate, args.own_team)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.jury = jury

    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        datefmt='%H:%M:%S', level=logging.INFO)
    logging.info('Жюри слушает http://%s:%d/flag', args.host, args.port)
    logging.info('  стата:    http://%s:%d/stats', args.host, args.port)
    logging.info('  очки:     %d..%d, error-rate=%.2f',
                 args.points[0], args.points[1], args.error_rate)
    if args.own_team:
        logging.info('  свой:     %s (всегда 403)', args.own_team)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info('пока!')


if __name__ == '__main__':
    main()
