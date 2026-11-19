"""交互设置管理员；允许重设管理员密码，禁止把填报账号升级为管理员。"""
import getpass
import hashlib

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from reporting.models import LoginThrottle


class Command(BaseCommand):
    help = '交互设置管理员账号和密码。'
    requires_migrations_checks = True

    def handle(self, *args, **options):
        User = get_user_model()
        admins = list(User.objects.filter(is_superuser=True).order_by('username').values_list('username', flat=True))
        if admins:
            self.stdout.write('已有管理员：' + '、'.join(admins))
            self.stdout.write('输入已有管理员账号可重设其密码；输入新账号可新建管理员。')
        else:
            self.stdout.write('尚未创建管理员，请设置登录账号和密码。')
        default = admins[0] if len(admins) == 1 else 'admin'
        try:
            username = input(f'管理员账号 [{default}]：').strip() or default
            User._meta.get_field('username').clean(username, None)
            existing = User.objects.filter(username=username).first()
            if existing and (not existing.is_superuser or hasattr(existing, 'reporting_unit')):
                raise CommandError('该账号是企业填报账号，请使用其他管理员账号名。')
            self.stdout.write(('重设密码：' if existing else '创建管理员：') + username)
            while True:
                password = getpass.getpass('管理员密码（输入时不显示）：')
                confirm = getpass.getpass('再次输入密码：')
                if password != confirm:
                    self.stderr.write('两次密码不一致，请重新输入。')
                    continue
                try:
                    validate_password(password, existing or User(username=username))
                except ValidationError as exc:
                    self.stderr.write('；'.join(exc.messages))
                    continue
                break
        except ValidationError as exc:
            raise CommandError('；'.join(exc.messages))
        except (EOFError, KeyboardInterrupt):
            raise CommandError('操作已取消，账号密码未修改。')

        try:
            with transaction.atomic():
                user = User.objects.select_for_update().filter(username=username).first()
                if user:
                    if not user.is_superuser or hasattr(user, 'reporting_unit'):
                        raise CommandError('该账号已被企业填报账号占用，请选择其他账号名。')
                    user.set_password(password)
                    user.is_active = user.is_staff = True
                    user.save(update_fields=['password', 'is_active', 'is_staff'])
                else:
                    User.objects.create_superuser(username=username, password=password)
                key = hashlib.sha256(username.encode()).hexdigest()
                LoginThrottle.objects.filter(key=key).delete()
        except IntegrityError:
            raise CommandError('账号已被其他操作创建，请重新运行脚本。')
        self.stdout.write(self.style.SUCCESS(f'管理员已设置。登录账号：{username}'))
        self.stdout.write('密码已保存，可运行 ./start.sh 启动服务。')
