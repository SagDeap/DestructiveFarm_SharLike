import glob
import os
import sqlite3
from datetime import datetime

from server import app, config_editor, database, reloader


BACKUP_DIR = os.path.join(app.root_path, 'backups')


class MaintenanceError(Exception):
    pass


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def _file_info(path):
    stat = os.stat(path)
    return {
        'name': os.path.basename(path),
        'path': path,
        'size': stat.st_size,
        'mtime': datetime.fromtimestamp(stat.st_mtime),
    }


def _safe_backup_path(directory, filename):
    filename = os.path.basename(filename)
    path = os.path.abspath(os.path.join(directory, filename))
    directory = os.path.abspath(directory)
    if not path.startswith(directory + os.sep):
        raise MaintenanceError('Некорректное имя бэкапа')
    if not os.path.exists(path):
        raise MaintenanceError('Бэкап не найден: {}'.format(filename))
    return path


def database_info():
    db_path = database.db_filename
    info = {
        'path': db_path,
        'exists': os.path.exists(db_path),
        'size': os.path.getsize(db_path) if os.path.exists(db_path) else 0,
        'wal_size': 0,
        'shm_size': 0,
    }

    wal_path = db_path + '-wal'
    shm_path = db_path + '-shm'
    if os.path.exists(wal_path):
        info['wal_size'] = os.path.getsize(wal_path)
    if os.path.exists(shm_path):
        info['shm_size'] = os.path.getsize(shm_path)
    return info


def list_config_backups():
    pattern = reloader.config_path + '.*.bak'
    return sorted((_file_info(path) for path in glob.glob(pattern)),
                  key=lambda item: item['mtime'], reverse=True)


def list_database_backups():
    _ensure_backup_dir()
    pattern = os.path.join(BACKUP_DIR, 'flags-*.sqlite')
    return sorted((_file_info(path) for path in glob.glob(pattern)),
                  key=lambda item: item['mtime'], reverse=True)


def create_database_backup():
    _ensure_backup_dir()
    backup_path = os.path.join(
        BACKUP_DIR, 'flags-{}.sqlite'.format(datetime.now().strftime('%Y%m%d-%H%M%S-%f')))

    source = database.get(context_bound=False)
    try:
        dest = sqlite3.connect(backup_path)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()

    return backup_path


def restore_config_backup(filename):
    path = config_backup_path(filename)
    with open(path, 'r', encoding='utf-8') as f:
        source = f.read()
    return config_editor.save_source(source)


def config_backup_path(filename):
    if not (filename.startswith('config.py.') and filename.endswith('.bak')):
        raise MaintenanceError('Некорректное имя бэкапа конфига')
    return _safe_backup_path(os.path.dirname(reloader.config_path), filename)


def database_backup_path(filename):
    if not (filename.startswith('flags-') and filename.endswith('.sqlite')):
        raise MaintenanceError('Некорректное имя бэкапа БД')
    return _safe_backup_path(BACKUP_DIR, filename)


def checkpoint_wal():
    db = database.get(context_bound=False)
    try:
        return db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall()
    finally:
        db.close()


def vacuum_database():
    db = database.get(context_bound=False)
    try:
        db.execute('VACUUM')
    finally:
        db.close()
