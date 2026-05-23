import ast
import os
import re
import shutil
from datetime import datetime

from server import app, reloader


REQUIRED_KEYS = [
    'TEAMS',
    'FLAG_FORMAT',
    'SYSTEM_PROTOCOL',
    'SUBMIT_FLAG_LIMIT',
    'SUBMIT_PERIOD',
    'FLAG_LIFETIME',
    'SERVER_USERNAME',
    'SERVER_PASSWORD',
    'ENABLE_API_AUTH',
    'API_TOKEN',
]

PROTOCOL_REQUIRED_KEYS = {
    'ctf01d_http': ['SYSTEM_URL', 'TEAM_ID'],
    'ructf_tcp': ['SYSTEM_HOST', 'SYSTEM_PORT'],
    'ructf_http': ['SYSTEM_URL', 'SYSTEM_TOKEN'],
    'volgactf': ['SYSTEM_HOST'],
    'forcad_tcp': ['SYSTEM_HOST', 'SYSTEM_PORT', 'TEAM_TOKEN'],
}

QUICK_KEYS = [
    'TEAMS',
    'FLAG_FORMAT',
    'SYSTEM_PROTOCOL',
    'SYSTEM_URL',
    'TEAM_ID',
    'SUBMIT_FLAG_LIMIT',
    'SUBMIT_PERIOD',
    'FLAG_LIFETIME',
    'SERVER_USERNAME',
    'SERVER_PASSWORD',
    'ENABLE_API_AUTH',
    'API_TOKEN',
]


class ConfigEditError(Exception):
    pass


def read_source():
    with open(reloader.config_path, 'r', encoding='utf-8') as f:
        return f.read()


def validate_source(source):
    try:
        code = compile(source, reloader.config_path, 'exec')
    except SyntaxError as e:
        raise ConfigEditError('Синтаксическая ошибка на строке {}: {}'.format(e.lineno, e.msg))

    namespace = {'__file__': reloader.config_path}
    try:
        exec(code, namespace)
    except Exception as e:
        raise ConfigEditError('Не удалось выполнить config.py: {}: {}'.format(type(e).__name__, e))

    if 'CONFIG' not in namespace:
        raise ConfigEditError('Переменная CONFIG не найдена')

    config = namespace['CONFIG']
    validate_config(config)
    return config


def validate_config(config):
    if not isinstance(config, dict):
        raise ConfigEditError('CONFIG должен быть словарем')

    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ConfigEditError('Не хватает ключей конфига: {}'.format(', '.join(missing)))

    if not isinstance(config['TEAMS'], dict):
        raise ConfigEditError('TEAMS должен быть словарем')
    for name, addr in config['TEAMS'].items():
        if not isinstance(name, str) or not isinstance(addr, str):
            raise ConfigEditError('Ключи и значения TEAMS должны быть строками')

    if not isinstance(config['FLAG_FORMAT'], str):
        raise ConfigEditError('FLAG_FORMAT должен быть строкой')
    try:
        re.compile(config['FLAG_FORMAT'])
    except re.error as e:
        raise ConfigEditError('Некорректная регулярка FLAG_FORMAT: {}'.format(e))

    protocol = config['SYSTEM_PROTOCOL']
    if not isinstance(protocol, str):
        raise ConfigEditError('SYSTEM_PROTOCOL должен быть строкой')
    if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', protocol) is None:
        raise ConfigEditError('SYSTEM_PROTOCOL содержит некорректное имя модуля')

    protocol_path = os.path.join(app.root_path, 'protocols', protocol + '.py')
    if not os.path.exists(protocol_path):
        raise ConfigEditError('Модуль протокола не найден: {}'.format(protocol))

    for key in PROTOCOL_REQUIRED_KEYS.get(protocol, []):
        if key not in config:
            raise ConfigEditError('Для протокола {} не хватает {}'.format(protocol, key))

    for key in ['SUBMIT_FLAG_LIMIT', 'SUBMIT_PERIOD', 'FLAG_LIFETIME']:
        if not isinstance(config[key], int) or config[key] <= 0:
            raise ConfigEditError('{} должен быть положительным целым числом'.format(key))

    for key in ['SERVER_USERNAME', 'SERVER_PASSWORD', 'API_TOKEN']:
        if not isinstance(config[key], str):
            raise ConfigEditError('{} должен быть строкой'.format(key))

    if not isinstance(config['ENABLE_API_AUTH'], bool):
        raise ConfigEditError('ENABLE_API_AUTH должен быть bool')


def quick_values_from_config(config):
    return {
        'teams': '\n'.join('{}={}'.format(name, addr)
                           for name, addr in config.get('TEAMS', {}).items()),
        'excluded_teams': '',
        'flag_format': config.get('FLAG_FORMAT', ''),
        'system_protocol': config.get('SYSTEM_PROTOCOL', ''),
        'system_url': config.get('SYSTEM_URL', ''),
        'team_id': config.get('TEAM_ID', ''),
        'submit_flag_limit': config.get('SUBMIT_FLAG_LIMIT', ''),
        'submit_period': config.get('SUBMIT_PERIOD', ''),
        'flag_lifetime': config.get('FLAG_LIFETIME', ''),
        'server_username': config.get('SERVER_USERNAME', ''),
        'server_password': config.get('SERVER_PASSWORD', ''),
        'enable_api_auth': config.get('ENABLE_API_AUTH', False),
        'api_token': config.get('API_TOKEN', ''),
    }


def quick_values_from_form(form):
    return {
        'teams': form.get('teams', ''),
        'excluded_teams': form.get('excluded_teams', ''),
        'flag_format': form.get('flag_format', ''),
        'system_protocol': form.get('system_protocol', ''),
        'system_url': form.get('system_url', ''),
        'team_id': form.get('team_id', ''),
        'submit_flag_limit': form.get('submit_flag_limit', ''),
        'submit_period': form.get('submit_period', ''),
        'flag_lifetime': form.get('flag_lifetime', ''),
        'server_username': form.get('server_username', ''),
        'server_password': form.get('server_password', ''),
        'enable_api_auth': 'enable_api_auth' in form,
        'api_token': form.get('api_token', ''),
    }


def build_source_from_quick_form(source, form):
    values = quick_values_from_form(form)
    teams = parse_teams(values['teams'])
    teams = exclude_teams(teams, values['excluded_teams'])
    replacements = {
        'TEAMS': teams,
        'FLAG_FORMAT': values['flag_format'],
        'SYSTEM_PROTOCOL': values['system_protocol'],
        'SYSTEM_URL': values['system_url'],
        'TEAM_ID': values['team_id'],
        'SUBMIT_FLAG_LIMIT': parse_positive_int(values['submit_flag_limit'], 'SUBMIT_FLAG_LIMIT'),
        'SUBMIT_PERIOD': parse_positive_int(values['submit_period'], 'SUBMIT_PERIOD'),
        'FLAG_LIFETIME': parse_positive_int(values['flag_lifetime'], 'FLAG_LIFETIME'),
        'SERVER_USERNAME': values['server_username'],
        'SERVER_PASSWORD': values['server_password'],
        'ENABLE_API_AUTH': values['enable_api_auth'],
        'API_TOKEN': values['api_token'],
    }
    return replace_config_values(source, replacements)


def parse_teams(text):
    teams = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        if '=' in line:
            name, addr = line.split('=', 1)
        elif ':' in line:
            name, addr = line.split(':', 1)
        else:
            raise ConfigEditError('Строка команды {} должна быть в формате name=address'.format(line_no))

        name = name.strip()
        addr = addr.strip()
        if not name or not addr:
            raise ConfigEditError('В строке команды {} пустое имя или адрес'.format(line_no))
        teams[name] = addr
    return teams


def parse_excluded_teams(text):
    result = []
    for item in re.split(r'[\s,]+', text.strip()):
        item = item.strip()
        if item:
            result.append(item.lower())
    return result


def exclude_teams(teams, text):
    tokens = parse_excluded_teams(text)
    if not tokens:
        return teams

    return {name: addr for name, addr in teams.items()
            if not is_excluded_team(name, addr, tokens)}


def is_excluded_team(name, addr, tokens):
    name_lower = name.lower()
    addr_lower = addr.lower()
    name_number = extract_last_number(name)
    addr_last_octet = addr.rsplit('.', 1)[-1] if '.' in addr else None

    for token in tokens:
        normalized = token[1:] if token.startswith('#') else token
        if token in (name_lower, addr_lower):
            return True
        if normalized == name_number or normalized == addr_last_octet:
            return True
    return False


def extract_last_number(text):
    match = re.search(r'(\d+)(?!.*\d)', text)
    return match.group(1) if match else None


def parse_positive_int(value, key):
    try:
        value = int(value)
    except ValueError:
        raise ConfigEditError('{} должен быть положительным целым числом'.format(key))
    if value <= 0:
        raise ConfigEditError('{} должен быть положительным целым числом'.format(key))
    return value


def replace_config_values(source, values):
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ConfigEditError('Синтаксическая ошибка на строке {}: {}'.format(e.lineno, e.msg))
    config_node = find_config_dict(tree)
    if config_node is None:
        raise ConfigEditError('Словарь CONFIG не найден')
    if not hasattr(config_node, 'end_lineno'):
        raise ConfigEditError('Быстрый редактор требует Python 3.8+ на сервере фермы')

    line_offsets = get_line_offsets(source)
    replacements = []
    seen = set()
    for key_node, value_node in zip(config_node.keys, config_node.values):
        if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
            continue

        key = key_node.value
        if key not in values:
            continue

        start = absolute_pos(line_offsets, value_node.lineno, value_node.col_offset)
        end = absolute_pos(line_offsets, value_node.end_lineno, value_node.end_col_offset)
        replacements.append((start, end, format_python_value(values[key], value_node.col_offset)))
        seen.add(key)

    missing = [key for key in values if key not in seen]
    if missing:
        raise ConfigEditError('Быстрый редактор не нашел ключи: {}'.format(', '.join(missing)))

    for start, end, replacement in sorted(replacements, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def find_config_dict(tree):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == 'CONFIG'
               for target in node.targets):
            if isinstance(node.value, ast.Dict):
                return node.value
            raise ConfigEditError('Для быстрого редактирования CONFIG должен быть литералом словаря')
    return None


def get_line_offsets(source):
    offsets = []
    offset = 0
    for line in source.splitlines(keepends=True):
        offsets.append(offset)
        offset += len(line)
    offsets.append(offset)
    return offsets


def absolute_pos(line_offsets, lineno, col):
    return line_offsets[lineno - 1] + col


def format_python_value(value, value_col):
    if isinstance(value, dict):
        if not value:
            return '{}'

        indent = ' ' * value_col
        lines = ['{']
        for key, item in value.items():
            lines.append(indent + '    {!r}: {!r},'.format(key, item))
        lines.append(indent + '}')
        return '\n'.join(lines)

    return repr(value)


def save_source(source):
    validate_source(source)

    config_path = reloader.config_path
    backup_path = '{}.{}.bak'.format(
        config_path, datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    tmp_path = config_path + '.tmp'

    shutil.copy2(config_path, backup_path)
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(source)
        os.replace(tmp_path, config_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return backup_path
