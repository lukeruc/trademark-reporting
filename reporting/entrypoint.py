"""启动时生成并保存登录二维码，页面只读取已保存的地址和图片。"""
import hashlib
import os
from tempfile import NamedTemporaryFile

import qrcode
from django.conf import settings
from django.urls import reverse

from config.runtime import primary_address
from .models import EntryConfig


def qr_image_path(url):
    name = hashlib.sha256(url.encode()).hexdigest() + '.png'
    return settings.BASE_DIR / 'var' / 'entry-qr' / name


def prepare_entry_qr(port):
    address = primary_address(settings.LOCAL_ADDRESSES)
    host = f'[{address}]' if ':' in address else address
    scheme = 'https' if settings.HTTPS_ENABLED else 'http'
    url = f'{scheme}://{host}:{port}{reverse("login")}'
    path = qr_image_path(url)
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
        qr.add_data(url)
        qr.make(fit=True)
        with NamedTemporaryFile(dir=path.parent, suffix='.png', delete=False) as output:
            temporary = output.name
            try:
                qr.make_image(fill_color='#1D2380', back_color='white').save(output, format='PNG')
                output.flush()
                os.fsync(output.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    # 同一地址反复启动时复用图片与记录；IP 或端口变化后，在启动时更新。
    config, created = EntryConfig.objects.get_or_create(pk=1, defaults={'url': url})
    if not created and config.url != url:
        config.url = url
        config.save(update_fields=['url', 'updated_at'])
    return config
