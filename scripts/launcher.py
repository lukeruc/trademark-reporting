"""两个 Shell 入口共用的预检及启动过程；只使用调用者手动激活的环境。"""
import importlib.util
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def check_environment():
    prefix = os.getenv('CONDA_PREFIX')
    if not prefix or Path(prefix).resolve() != Path(sys.prefix).resolve() or sys.version_info[:2] != (3, 12):
        raise RuntimeError('请先手动激活项目的 Python 3.12 conda 环境，再运行脚本。')
    packages = ['django', 'psycopg', 'openpyxl', 'PIL', 'gunicorn', 'qrcode', 'pypinyin', 'whitenoise']
    if any(importlib.util.find_spec(name) is None for name in packages):
        raise RuntimeError('当前环境缺少项目依赖。请在该 conda 环境运行：python -m pip install -r requirements.txt')


def bind_address():
    try:
        port = int(os.getenv('APP_PORT', '7070'))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise RuntimeError('APP_PORT 请填写 1—65535 的网页访问端口。')
    return f'0.0.0.0:{port}'


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('admin', 'serve'):
        raise RuntimeError('请使用 setup_admin.sh 或 start.sh。')
    mode = sys.argv[1]
    check_environment()
    bind = bind_address() if mode == 'serve' else None
    from config.database import ensure_database
    print('正在准备应用数据库……', flush=True)
    ensure_database(ROOT)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    from django.db.utils import OperationalError

    print('正在检查数据库并准备数据表……', flush=True)
    try:
        call_command('migrate', interactive=False, verbosity=0)
    except OperationalError:
        raise RuntimeError('应用数据库连接或初始化失败，请查看 var/database.log 并确认 var 目录可写。')

    if mode == 'admin':
        call_command('configure_admin')
        return

    from reporting.models import User
    if not User.objects.filter(is_superuser=True, is_active=True).exists():
        raise RuntimeError('尚未设置管理员，请先运行 ./setup_admin.sh。')
    call_command('check')
    call_command('collectstatic', interactive=False, verbosity=0)
    from reporting.entrypoint import prepare_entry_qr
    prepare_entry_qr(int(bind.rsplit(':', 1)[1]))
    connections.close_all()
    print(f'正在启动服务，监听 {bind}。按 Ctrl+C 停止；在 tmux 中分离会话可保持运行。', flush=True)
    for address in settings.LOCAL_ADDRESSES:
        print(f'访问地址：http://{address}:{int(os.getenv("APP_PORT", "7070"))}/', flush=True)
    os.execv(sys.executable, [
        sys.executable, '-m', 'gunicorn', 'config.wsgi:application',
        '--bind', bind, '--workers', '3', '--timeout', '120',
        '--access-logfile', '-', '--error-logfile', '-',
    ])


if __name__ == '__main__':
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print('\n操作已取消。', file=sys.stderr)
        raise SystemExit(1)
    except Exception as error:
        print(f'操作未完成：{error}', file=sys.stderr)
        raise SystemExit(1)
