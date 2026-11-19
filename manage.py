#!/usr/bin/env python
import os
import sys

if __name__ == '__main__':
    if sys.version_info[:2] != (3, 12):
        raise SystemExit('本项目使用 Python 3.12，请先激活项目 conda 环境。')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
