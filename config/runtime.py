"""运行时自动维护的配置，无需手工填写。"""
import fcntl
import ipaddress
import os
from pathlib import Path
import secrets
import socket
import struct

from django.core.exceptions import ImproperlyConfigured


def local_addresses():
    # 直接读取本机网卡，不访问外部网络，也不要求用户填写服务器 IP。
    addresses = {'127.0.0.1'}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            for _, name in socket.if_nameindex():
                try:
                    data = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack('256s', name.encode()[:15]))
                    addresses.add(socket.inet_ntoa(data[20:24]))
                except OSError:
                    continue
    except OSError:
        pass
    return sorted(addresses, key=lambda value: (ipaddress.ip_address(value).is_loopback, value))


def primary_address(addresses):
    """优先使用默认路由所在网卡，避免选中 Docker 等虚拟网卡的地址。"""
    try:
        routes = [line.split() for line in Path('/proc/net/route').read_text().splitlines()[1:]]
        defaults = sorted((row for row in routes if row[1] == '00000000' and int(row[3], 16) & 1),
                          key=lambda row: int(row[6]))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            for route in defaults:
                data = fcntl.ioctl(probe.fileno(), 0x8915, struct.pack('256s', route[0].encode()[:15]))
                address = socket.inet_ntoa(data[20:24])
                if address in addresses and not ipaddress.ip_address(address).is_loopback:
                    return address
    except (OSError, ValueError, IndexError):
        pass
    return next((value for value in addresses
                 if not ipaddress.ip_address(value).is_loopback and not ipaddress.ip_address(value).is_link_local),
                '127.0.0.1')


def load_secret(base_dir):
    """由所有命令和工作进程共用持久密钥，首次生成；兼容原环境中的已有密钥。"""
    directory = Path(base_dir) / 'var'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'secret.key'
    supplied = os.getenv('DJANGO_SECRET_KEY', '').strip()
    if supplied.startswith('replace-with-'):
        supplied = ''
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, 'r+', encoding='utf-8') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            os.fchmod(stream.fileno(), 0o600)
            existing = stream.read().strip()
            value = supplied or existing or secrets.token_urlsafe(50)
            if value != existing:
                stream.seek(0)
                stream.write(value + '\n')
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
            return value
    except OSError as exc:
        raise ImproperlyConfigured('无法读写 var/secret.key，请确认项目 var 目录可写。') from exc
