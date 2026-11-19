"""根据企业全称生成账号主体和随机初始密码。"""
import secrets
import string
import unicodedata

from django.core.exceptions import ValidationError
from django.utils.crypto import salted_hmac
from pypinyin import Style, lazy_pinyin

USERNAME_MAX_LENGTH = 150
PASSWORD_LENGTH = 8


def account_version(user):
    return salted_hmac('reporting.account-version', f'{user.username}:{user.password}').hexdigest()


def enterprise_initials(name):
    # 词组读音处理多音字；英文、数字保留，空格和标点不进入登录账号。
    normalized = unicodedata.normalize('NFKC', name)
    initials = ''.join(lazy_pinyin(
        normalized, style=Style.FIRST_LETTER,
        errors=lambda text: list(text),
    )).lower()
    result = ''.join(char for char in initials if char in string.ascii_lowercase + string.digits)
    if not result:
        raise ValidationError('企业名称无法生成账号，请核对企业全称。')
    if len(result) > USERNAME_MAX_LENGTH:
        raise ValidationError('企业名称生成的账号超过 150 个字符，请核对企业全称。')
    return result


def random_password():
    alphabet = string.ascii_letters + string.digits
    while True:
        value = ''.join(secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH))
        if any(char.isalpha() for char in value) and any(char.isdigit() for char in value):
            return value
