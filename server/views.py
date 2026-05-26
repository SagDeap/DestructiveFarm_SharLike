import re
import time
from datetime import datetime

from flask import jsonify, render_template, request, send_file

from server import app, auth, config_editor, database, health, maintenance, reloader
from server.models import FlagStatus


STATUS_LABELS = {
    FlagStatus.QUEUED.name: 'В очереди',
    FlagStatus.SKIPPED.name: 'Пропущен',
    FlagStatus.ACCEPTED.name: 'Принят',
    FlagStatus.REJECTED.name: 'Отклонен',
}


@app.template_filter('timestamp_to_datetime')
def timestamp_to_datetime(s):
    return datetime.fromtimestamp(s)


@app.template_filter('status_label')
def status_label(status):
    if status is None:
        return '-'
    return STATUS_LABELS.get(str(status), str(status))


SHORT_FALLBACK = {
    FlagStatus.ACCEPTED.name: '✓',
    FlagStatus.REJECTED.name: '✗',
    FlagStatus.QUEUED.name: '…',
    FlagStatus.SKIPPED.name: '—',
}


@app.template_filter('short_response')
def short_response(response, status):
    text = (response or '').strip()
    if text:
        match = re.match(r'(\d{3})\b', text)
        if match:
            return match.group(1)
        first = text.split(None, 1)[0]
        if len(first) <= 14:
            return first
        return first[:13] + '…'
    return SHORT_FALLBACK.get(status, '?')


PROTOCOL_DESCRIPTIONS = {
    'ctf01d_http': lambda flag, c: {
        'kind': 'HTTP',
        'method': 'GET',
        'target': '{}?teamid={}&flag={}'.format(c.get('SYSTEM_URL', '?'), c.get('TEAM_ID', '?'), flag),
        'headers': {},
        'body': None,
        'note': 'Один HTTP-запрос на флаг. 200 = принят, 403 = отклонен.',
    },
    'ructf_http': lambda flag, c: {
        'kind': 'HTTP',
        'method': 'PUT',
        'target': c.get('SYSTEM_URL', '?'),
        'headers': {'X-Team-Token': c.get('SYSTEM_TOKEN', '?')},
        'body': '[..., "{}", ...]'.format(flag),
        'note': 'Все флаги пачкой одним JSON-запросом.',
    },
    'ructf_tcp': lambda flag, c: {
        'kind': 'TCP',
        'method': '',
        'target': '{}:{}'.format(c.get('SYSTEM_HOST', '?'), c.get('SYSTEM_PORT', '?')),
        'headers': {},
        'body': '{}\\n'.format(flag),
        'note': 'TCP-сессия. После greeting ферма пишет флаги построчно.',
    },
    'forcad_tcp': lambda flag, c: {
        'kind': 'TCP',
        'method': '',
        'target': '{}:{}'.format(c.get('SYSTEM_HOST', '?'), c.get('SYSTEM_PORT', '?')),
        'headers': {'TEAM_TOKEN': c.get('TEAM_TOKEN', '?')},
        'body': '{}\\n'.format(flag),
        'note': 'TCP. Сначала отдается TEAM_TOKEN, затем флаги построчно.',
    },
    'volgactf': lambda flag, c: {
        'kind': 'SDK',
        'method': 'attack(...)',
        'target': 'themis.finals.attack.helper.Helper("{}")'.format(c.get('SYSTEM_HOST', '?')),
        'headers': {},
        'body': flag,
        'note': 'Themis Helper SDK, флаги отправляются пачкой.',
    },
}


def _describe_request(protocol, flag, config):
    builder = PROTOCOL_DESCRIPTIONS.get(protocol)
    if builder is None:
        return {
            'kind': '?', 'method': '', 'target': '', 'headers': {}, 'body': None,
            'note': 'Описание для протокола "{}" не задано.'.format(protocol),
        }
    return builder(flag, config)


@app.route('/ui/flag_details')
@auth.auth_required
def flag_details():
    flag = request.args.get('flag', '')
    if not flag:
        return ('flag required', 400)

    rows = database.query('SELECT * FROM flags WHERE flag = ?', (flag,))
    if not rows:
        return ('', 404)
    row = rows[0]

    config = reloader.get_config()
    protocol = config.get('SYSTEM_PROTOCOL', '')
    req = _describe_request(protocol, flag, config)
    return render_template('_flag_details.html', row=row, req=req, protocol=protocol)


@app.route('/')
@auth.auth_required
def index():
    distinct_values = {}
    for column in ['sploit', 'status', 'team']:
        rows = database.query('SELECT DISTINCT {} FROM flags ORDER BY {}'.format(column, column))
        distinct_values[column] = [item[column] for item in rows]

    config = reloader.get_config()

    server_tz_name = time.strftime('%Z')
    if server_tz_name.startswith('+'):
        server_tz_name = 'UTC' + server_tz_name

    return render_template('index.html',
                           flag_format=config['FLAG_FORMAT'],
                           distinct_values=distinct_values,
                           server_tz_name=server_tz_name)


FORM_DATETIME_FORMAT = '%Y-%m-%d %H:%M'
FLAGS_PER_PAGE = 30
STATUS_NAMES = [item.name for item in FlagStatus]
STATUS_KEYS = [(item.name, item.name.lower()) for item in FlagStatus]
STATS_TOP_LIMIT = 30


def _send_file(path, filename):
    try:
        return send_file(path, as_attachment=True, download_name=filename)
    except TypeError:
        return send_file(path, as_attachment=True, attachment_filename=filename)


def _format_age(seconds):
    if seconds is None:
        return '-'
    seconds = int(max(0, seconds))
    if seconds < 60:
        return '{}с'.format(seconds)
    if seconds < 3600:
        return '{}м {}с'.format(seconds // 60, seconds % 60)
    return '{}ч {}м'.format(seconds // 3600, (seconds % 3600) // 60)


def _count_flags(where_sql='', args=()):
    rows = database.query('SELECT COUNT(*) AS count FROM flags ' + where_sql, args)
    return rows[0]['count']


def _status_counts(where_sql='', args=()):
    counts = {status: 0 for status in STATUS_NAMES}
    rows = database.query(
        'SELECT status, COUNT(*) AS count FROM flags ' + where_sql + ' GROUP BY status',
        args)
    for item in rows:
        counts[item['status']] = item['count']
    return counts


def _group_stats(column):
    status_selects = []
    args = []
    for status, key in STATUS_KEYS:
        status_selects.append(
            'SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS {}'.format(key))
        args.append(status)

    sql = ('SELECT {column} AS name, COUNT(*) AS total, {status_selects} '
           'FROM flags GROUP BY {column} '
           'ORDER BY accepted DESC, total DESC, name LIMIT ?').format(
               column=column, status_selects=', '.join(status_selects))
    args.append(STATS_TOP_LIMIT)

    result = []
    for row in database.query(sql, args):
        item = dict(row)
        checked = item['accepted'] + item['rejected']
        item['accept_rate'] = None if checked == 0 else item['accepted'] * 100 / checked
        result.append(item)
    return result


@app.route('/stats')
@auth.auth_required
def stats():
    cur_time = round(time.time())
    recent_periods = [
        ('За 5 минут', cur_time - 5 * 60),
        ('За час', cur_time - 60 * 60),
    ]

    return render_template('stats.html',
                           total_count=_count_flags(),
                           status_counts=_status_counts(),
                           recent_counts=[
                               {'label': label,
                                'count': _count_flags('WHERE time >= ?', (since,)),
                                'statuses': _status_counts('WHERE time >= ?', (since,))}
                               for label, since in recent_periods
                           ],
                           status_keys=STATUS_KEYS,
                           sploit_stats=_group_stats('sploit'),
                           team_stats=_group_stats('team'))


def _health_context():
    config = reloader.get_config()
    snapshot = health.snapshot()
    cur_time = round(time.time())
    queued_count = _count_flags('WHERE status = ?', (FlagStatus.QUEUED.name,))

    oldest_rows = database.query(
        'SELECT MIN(time) AS oldest FROM flags WHERE status = ?',
        (FlagStatus.QUEUED.name,))
    oldest_queued_at = oldest_rows[0]['oldest']
    oldest_queued_age = None if oldest_queued_at is None else cur_time - oldest_queued_at

    lifetime = config['FLAG_LIFETIME']
    danger_since = cur_time - max(0, lifetime - 15)
    expiring_soon = _count_flags(
        'WHERE status = ? AND time <= ?',
        (FlagStatus.QUEUED.name, danger_since))

    last_tick_age = None
    if snapshot['last_tick_at'] is not None:
        last_tick_age = time.time() - snapshot['last_tick_at']

    return {
        'config': config,
        'status_keys': STATUS_KEYS,
        'health': snapshot,
        'queued_count': queued_count,
        'oldest_queued_age': _format_age(oldest_queued_age),
        'expiring_soon': expiring_soon,
        'last_tick_age': _format_age(last_tick_age),
        'now': time.time(),
    }


@app.route('/health')
@auth.auth_required
def health_page():
    return render_template('health.html', **_health_context())


@app.route('/ui/health_partial')
@auth.auth_required
def health_partial():
    return render_template('_health_live.html', **_health_context())


@app.route('/ui/loop_toast')
@auth.auth_required
def loop_toast():
    config = reloader.get_config()
    snapshot = health.snapshot()
    cur_time = time.time()

    last_tick = snapshot.get('last_tick_at')
    stale_threshold = max(15, config['SUBMIT_PERIOD'] * 4)
    is_stale = last_tick is not None and (cur_time - last_tick) > stale_threshold
    last_error = snapshot.get('last_error')

    if not last_error and not is_stale:
        return ''

    return render_template('_loop_toast.html',
                           last_error=last_error,
                           is_stale=is_stale,
                           tick_age=None if last_tick is None else cur_time - last_tick,
                           consecutive_errors=snapshot.get('consecutive_error_rounds', 0))


def _render_config_editor(config_source=None, quick_values=None, message=None, error=None):
    if config_source is None:
        config_source = config_editor.read_source()
    if quick_values is None:
        quick_values = config_editor.quick_values_from_config(reloader.get_config())

    return render_template('config.html',
                           config_path=reloader.config_path,
                           config_source=config_source,
                           quick=quick_values,
                           message=message,
                           error=error)


@app.route('/config', methods=['GET', 'POST'])
@auth.auth_required
def config_page():
    if request.method == 'GET':
        return _render_config_editor()

    mode = request.form.get('mode')
    current_source = config_editor.read_source()
    config_source = current_source
    quick_values = None

    try:
        if mode == 'quick':
            quick_values = config_editor.quick_values_from_form(request.form)
            config_source = config_editor.build_source_from_quick_form(
                current_source, request.form)
        elif mode == 'raw':
            config_source = request.form.get('config_source', '')
        else:
            raise config_editor.ConfigEditError('Неизвестный режим редактора конфига')

        backup_path = config_editor.save_source(config_source)
        config = reloader.get_config()
        return _render_config_editor(
            config_source=config_editor.read_source(),
            quick_values=config_editor.quick_values_from_config(config),
            message='Конфиг сохранен, бэкап: {}'.format(backup_path))
    except config_editor.ConfigEditError as e:
        if quick_values is None:
            quick_values = config_editor.quick_values_from_config(reloader.get_config())
        return _render_config_editor(config_source=config_source,
                                     quick_values=quick_values,
                                     error=str(e))


def _render_maintenance(message=None, error=None):
    return render_template('maintenance.html',
                           message=message,
                           error=error,
                           db_info=maintenance.database_info(),
                           config_backups=maintenance.list_config_backups(),
                           database_backups=maintenance.list_database_backups(),
                           config_path=reloader.config_path)


@app.route('/maintenance', methods=['GET', 'POST'])
@auth.auth_required
def maintenance_page():
    if request.method == 'GET':
        return _render_maintenance()

    action = request.form.get('action')
    try:
        if action == 'checkpoint_wal':
            maintenance.checkpoint_wal()
            message = 'SQLite WAL сброшен'
        elif action == 'vacuum_database':
            maintenance.vacuum_database()
            message = 'SQLite VACUUM выполнен'
        elif action == 'backup_database':
            path = maintenance.create_database_backup()
            message = 'Бэкап БД создан: {}'.format(path)
        elif action == 'clear_database':
            path = maintenance.clear_database()
            message = 'Таблица флагов очищена. Бэкап перед очисткой: {}'.format(path)
        elif action == 'restore_config':
            backup = request.form.get('backup', '')
            path = maintenance.restore_config_backup(backup)
            reloader.get_config()
            message = 'Конфиг восстановлен, предыдущая версия сохранена: {}'.format(path)
        else:
            raise maintenance.MaintenanceError('Неизвестное действие обслуживания')
        return _render_maintenance(message=message)
    except (maintenance.MaintenanceError, config_editor.ConfigEditError, OSError) as e:
        return _render_maintenance(error=str(e))


@app.route('/maintenance/download/config')
@auth.auth_required
def download_config():
    return _send_file(reloader.config_path, 'config.py')


@app.route('/maintenance/download/database')
@auth.auth_required
def download_database():
    path = maintenance.create_database_backup()
    return _send_file(path, 'flags.sqlite')


@app.route('/maintenance/download/config_backup/<filename>')
@auth.auth_required
def download_config_backup(filename):
    path = maintenance.config_backup_path(filename)
    return _send_file(path, filename)


@app.route('/maintenance/download/database_backup/<filename>')
@auth.auth_required
def download_database_backup(filename):
    path = maintenance.database_backup_path(filename)
    return _send_file(path, filename)


RETRYABLE_STATUSES = {FlagStatus.SKIPPED.name, FlagStatus.REJECTED.name}


def _flags_filters_from_form(form):
    conditions = []
    for column in ['sploit', 'status', 'team']:
        value = form.get(column, '')
        if value:
            conditions.append(('{} = ?'.format(column), value))
    for column in ['flag', 'checksystem_response']:
        value = form.get(column, '')
        if value:
            conditions.append(('INSTR(LOWER({}), ?)'.format(column), value.lower()))
    for param in ['time-since', 'time-until']:
        value = form.get(param, '').strip()
        if not value:
            continue
        try:
            timestamp = round(datetime.strptime(value, FORM_DATETIME_FORMAT).timestamp())
        except ValueError:
            continue
        sign = '>=' if param == 'time-since' else '<='
        conditions.append(('time {} ?'.format(sign), timestamp))
    return conditions


def _build_pagination(total_count, page_number):
    total_pages = max(1, (total_count + FLAGS_PER_PAGE - 1) // FLAGS_PER_PAGE)
    page_number = max(1, min(page_number, total_pages))
    first_shown = max(1, page_number - 3)
    last_shown = min(total_pages, page_number + 3)

    pages = []
    if first_shown > 1:
        pages.append({'label': '«', 'page': 1, 'active': False})
    for i in range(first_shown, last_shown + 1):
        pages.append({'label': str(i), 'page': i, 'active': i == page_number})
    if last_shown < total_pages:
        pages.append({'label': '»', 'page': total_pages, 'active': False})

    return {
        'pages': pages,
        'page_number': page_number,
        'total_pages': total_pages,
    }


SPARKLINE_WINDOW_MINUTES = 60


def _sparkline_buckets():
    cur_time = round(time.time())
    bucket_now = cur_time // 60
    since = (bucket_now - SPARKLINE_WINDOW_MINUTES + 1) * 60

    rows = database.query(
        "SELECT CAST(time / 60 AS INTEGER) AS bucket, "
        "       SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS accepted, "
        "       SUM(CASE WHEN status = ? THEN 1 ELSE 0 END) AS rejected "
        "FROM flags WHERE time >= ? GROUP BY bucket ORDER BY bucket",
        (FlagStatus.ACCEPTED.name, FlagStatus.REJECTED.name, since))

    by_bucket = {row['bucket']: row for row in rows}
    buckets = []
    total_accepted = 0
    total_rejected = 0
    for offset in range(SPARKLINE_WINDOW_MINUTES):
        bucket = bucket_now - SPARKLINE_WINDOW_MINUTES + 1 + offset
        row = by_bucket.get(bucket)
        accepted = row['accepted'] if row else 0
        rejected = row['rejected'] if row else 0
        total_accepted += accepted
        total_rejected += rejected
        checked = accepted + rejected
        rate = (accepted / checked * 100) if checked > 0 else None
        buckets.append({'offset': offset, 'rate': rate, 'checked': checked})
    return buckets, total_accepted, total_rejected


@app.route('/ui/sparkline_partial')
@auth.auth_required
def sparkline_partial():
    buckets, total_accepted, total_rejected = _sparkline_buckets()
    total_checked = total_accepted + total_rejected
    rate = (total_accepted / total_checked * 100) if total_checked > 0 else None
    return render_template('_sparkline.html',
                           buckets=buckets,
                           total_accepted=total_accepted,
                           total_rejected=total_rejected,
                           total_checked=total_checked,
                           accept_rate=rate,
                           window_minutes=SPARKLINE_WINDOW_MINUTES)


@app.route('/ui/flags_partial', methods=['POST'])
@auth.auth_required
def flags_partial():
    conditions = _flags_filters_from_form(request.form)
    try:
        page_number = max(1, int(request.form.get('page-number', '1')))
    except ValueError:
        page_number = 1

    if conditions:
        chunks, values = list(zip(*conditions))
        conditions_sql = 'WHERE ' + ' AND '.join(chunks)
        conditions_args = list(values)
    else:
        conditions_sql = ''
        conditions_args = []

    total_count = database.query(
        'SELECT COUNT(*) AS c FROM flags ' + conditions_sql, conditions_args)[0]['c']
    pagination = _build_pagination(total_count, page_number)

    sql = 'SELECT * FROM flags ' + conditions_sql + ' ORDER BY time DESC LIMIT ? OFFSET ?'
    args = conditions_args + [FLAGS_PER_PAGE, FLAGS_PER_PAGE * (pagination['page_number'] - 1)]
    flags = database.query(sql, args)

    return render_template('_flags_table.html',
                           flags=flags,
                           total_count=total_count,
                           pagination=pagination,
                           retryable=RETRYABLE_STATUSES)


@app.route('/ui/retry_flag', methods=['POST'])
@auth.auth_required
def retry_flag():
    flag = request.form.get('flag', '')
    if not flag:
        return ('', 400)

    db = database.get()
    db.execute(
        "UPDATE flags SET status = ?, checksystem_response = NULL "
        "WHERE flag = ? AND status IN ({})".format(','.join('?' * len(RETRYABLE_STATUSES))),
        [FlagStatus.QUEUED.name, flag] + list(RETRYABLE_STATUSES))
    db.commit()

    response = app.make_response('')
    response.headers['HX-Trigger'] = 'refreshFlags'
    return response


@app.route('/ui/post_flags_manual', methods=['POST'])
@auth.auth_required
def post_flags_manual():
    config = reloader.get_config()
    pattern = re.compile(config['FLAG_FORMAT'].strip('^$'))
    flags = pattern.findall(request.form['text'])

    cur_time = round(time.time())
    rows = [(item, 'Вручную', '*', cur_time, FlagStatus.QUEUED.name)
            for item in flags]

    db = database.get()
    db.executemany("INSERT OR IGNORE INTO flags (flag, sploit, team, time, status) "
                   "VALUES (?, ?, ?, ?, ?)", rows)
    db.commit()

    response = app.make_response('')
    response.headers['HX-Trigger'] = 'refreshFlags'
    return response
