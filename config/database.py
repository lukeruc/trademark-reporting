"""本应用专用 PostgreSQL 的启动及连接信息，无需用户填写数据库凭据。"""
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys

DATABASE_NAME = 'trademark'
DATABASE_USER = 'trademark'


def socket_directory(base_dir):
    identity = hashlib.sha256(str(Path(base_dir).resolve()).encode()).hexdigest()[:16]
    # PostgreSQL Unix 套接字有长度限制，不放在可能很长的中文项目路径下。
    return Path('/tmp') / f'trademark-pg-{os.getuid()}-{identity}'


def database_settings(base_dir):
    return {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': DATABASE_NAME,
        'USER': DATABASE_USER,
        'PASSWORD': '',
        'HOST': str(socket_directory(base_dir)),
        'PORT': '5432',
        'CONN_MAX_AGE': 60,
        'OPTIONS': {'connect_timeout': 10},
    }


def binary(name):
    path = Path(sys.executable).resolve().parent / name
    if not path.is_file():
        raise RuntimeError('当前 conda 环境缺少 PostgreSQL，请手动运行：conda install -c conda-forge postgresql=16')
    return path


def run(args, *, log=None):
    result = subprocess.run([str(x) for x in args], stdout=log or subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=60)
    if result.returncode:
        details = (result.stdout or '').strip()[-1500:]
        raise RuntimeError('内置数据库操作失败。请查看 var/database.log。' + ('\n' + details if details else ''))
    return result


def ensure_database(base_dir):
    """跨启动进程串行初始化，只在缺失时创建；不清空已有数据库。"""
    if os.geteuid() == 0:
        raise RuntimeError('请用普通 Linux 用户运行应用，不要使用 root 或 sudo 启动脚本。')
    base_dir = Path(base_dir).resolve()
    directory = base_dir / 'var'
    directory.mkdir(parents=True, exist_ok=True)
    data = directory / 'database'
    socket_dir = socket_directory(base_dir)
    pg_ctl, initdb = binary('pg_ctl'), binary('initdb')
    # 目录只有当前运行应用的 Linux 用户可进入；不开放数据库 TCP 监听。
    socket_dir.mkdir(mode=0o700, exist_ok=True)
    info = socket_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('内置数据库的套接字目录不可用，请检查 /tmp 目录权限。')
    socket_dir.chmod(0o700)
    descriptor = os.open(directory / 'database.lock', os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (data / 'PG_VERSION').exists():
            if data.exists() and any(data.iterdir()):
                raise RuntimeError('var/database 已存在未完成的初始化内容，请检查 var/database.log；程序不会删除其中数据。')
            with (directory / 'database.log').open('a') as log:
                run([initdb, '-D', data, '-U', DATABASE_USER, '--auth-local=trust',
                     '--auth-host=reject', '--no-locale', '--encoding=UTF8'], log=log)
        status = subprocess.run([str(pg_ctl), '-D', str(data), 'status'],
                                capture_output=True, text=True, timeout=10)
        if status.returncode:
            # 路径只来自内部计算，不使用用户输入拼接 PostgreSQL 启动参数。
            options = f"-c listen_addresses='' -c unix_socket_permissions=0700 -k {socket_dir} -p 5432"
            run([pg_ctl, '-D', data, '-l', directory / 'database.log', '-o', options, '-w', 'start'])
        import psycopg
        from psycopg import sql
        with psycopg.connect(host=str(socket_dir), port=5432, user=DATABASE_USER,
                             dbname='postgres', autocommit=True, connect_timeout=10) as connection:
            exists = connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (DATABASE_NAME,)).fetchone()
            if not exists:
                connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(DATABASE_NAME)))
