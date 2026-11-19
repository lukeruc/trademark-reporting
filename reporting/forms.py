import hashlib
from datetime import timedelta

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

from .credentials import enterprise_initials
from .models import Enterprise, LoginThrottle, ReportingUnit
from .schema import MARK_KINDS, SCHEMAS


class StyledForm:
    def style_fields(self):
        for field in self.fields.values():
            css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            if isinstance(field.widget, forms.CheckboxInput):
                css = 'form-check-input'
            field.widget.attrs.setdefault('class', css)


class LoginForm(StyledForm, AuthenticationForm):
    error_messages = {
        'invalid_login': '账号或密码不正确，请重新输入。',
        'inactive': '账号已停用，请联系管理员。',
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].label = '账号'
        self.fields['password'].label = '密码'
        self.style_fields()

    def clean(self):
        # 按账号限制尝试次数；数据库存储使 Gunicorn 多进程共享限制。
        name = str(self.cleaned_data.get('username', '')).strip()
        key = hashlib.sha256(name.encode()).hexdigest()
        now = timezone.now()
        with transaction.atomic():
            attempt, _ = LoginThrottle.objects.select_for_update().get_or_create(key=key, defaults={'since': now})
            if now - attempt.since >= timedelta(minutes=10):
                attempt.failures, attempt.since = 0, now
            if attempt.failures >= 10:
                raise ValidationError('登录尝试较多，请在 10 分钟后重试。')
            error = None
            try:
                cleaned = super().clean()
            except ValidationError as exc:
                error = exc
                attempt.failures += 1
            else:
                attempt.failures = 0
            attempt.save()
        if error:
            raise error
        return cleaned


class AccountForm(StyledForm, forms.Form):
    name = forms.CharField(label='报送单位全称', max_length=300,
                           help_text='账号取企业全称拼音首字母，小写；重名时自动加数字后缀。')
    kind = forms.ChoiceField(label='报送单位类型', choices=ReportingUnit.Kind.choices,
                             initial=ReportingUnit.Kind.SUBSIDIARY)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.style_fields()

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if ILLEGAL_CHARACTERS_RE.search(name):
            raise ValidationError('企业名称包含不可用字符，请删除后再保存。')
        enterprise_initials(name)
        if ReportingUnit.objects.filter(name=name).exists():
            raise ValidationError('该报送单位已创建账号。')
        return name

    def clean_kind(self):
        kind = self.cleaned_data['kind']
        if kind == ReportingUnit.Kind.HEADQUARTERS and ReportingUnit.objects.filter(kind=kind).exists():
            raise ValidationError('本部填报账号已经存在。')
        return kind


class EnterpriseForm(StyledForm, forms.ModelForm):
    class Meta:
        model = Enterprise
        fields = ['name', 'level', 'nature']

    def __init__(self, *args, unit, **kwargs):
        self.unit = unit
        super().__init__(*args, **kwargs)
        self.fields['level'].min_value = 2
        self.fields['level'].widget.attrs['min'] = 2
        self.fields['nature'].help_text = '可先保存草稿，统一提交前填写。'
        if self.instance.is_primary:
            self.fields['name'].disabled = True
            self.fields['level'].disabled = True
        self.style_fields()

    def clean_name(self):
        value = self.cleaned_data['name']
        if ILLEGAL_CHARACTERS_RE.search(value):
            raise ValidationError('企业名称包含不可用字符，请删除后再保存。')
        return value

    def clean(self):
        data = super().clean()
        level = data.get('level')
        if not self.instance.is_primary and level is not None and level < 4:
            self.add_error('level', '下属企业请填写实际产权级次，至少为四级。')
        if data.get('name') and self.unit.enterprises.filter(name=data['name']).exclude(pk=self.instance.pk).exists():
            self.add_error('name', '本报送单位内已有同名企业。')
        return data


class EntryForm(StyledForm, forms.Form):
    """草稿允许空值；正式提交使用相同字段解析并补做条件必填校验。"""

    def __init__(self, *args, kind, enterprise=None, stored=None, complete=False, **kwargs):
        self.kind = kind
        self.stored = stored or {}
        self.complete = complete
        initial = {k: v for k, v in self.stored.items()
                   if k not in [f.key for f in SCHEMAS[kind][1] if f.kind == 'image']}
        if not stored and enterprise:
            initial.update(holder_name=enterprise.name, holder_level=enterprise.level)
        kwargs.setdefault('initial', initial)
        super().__init__(*args, **kwargs)
        for spec in SCHEMAS[kind][1]:
            options = {'label': spec.label, 'required': False, 'help_text': spec.help}
            if spec.kind == 'choice':
                field = forms.ChoiceField(choices=[('', '请选择 / 不适用')] + [(x, x) for x in spec.options], **options)
            elif spec.kind == 'date':
                field = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), **options)
            elif spec.kind == 'level':
                field = forms.IntegerField(min_value=2, max_value=32767, **options)
            elif spec.kind == 'image':
                options['help_text'] = '可选，仅用于填写商标图样。支持 PNG、JPEG，最大 5 MB。'
                field = forms.ImageField(widget=forms.FileInput(attrs={'accept': 'image/png,image/jpeg'}), **options)
            else:
                widget = forms.Textarea(attrs={'rows': 3}) if spec.kind == 'textarea' else forms.TextInput()
                field = forms.CharField(max_length=20000 if spec.kind == 'textarea' else 500, widget=widget, **options)
            self.fields[spec.key] = field
            if spec.kind == 'image' and self.stored.get(spec.key):
                self.fields[f'{spec.key}_clear'] = forms.BooleanField(label='移除当前图样', required=False)
        self.style_fields()

    def has_image(self, key, data):
        return bool(data.get(key) or (self.stored.get(key) and not data.get(f'{key}_clear')))

    def clean(self):
        data = super().clean()
        for key, value in list(data.items()):
            if isinstance(value, str) and ILLEGAL_CHARACTERS_RE.search(value):
                self.add_error(key, '内容包含不可用字符，请删除后再保存。')
        for spec in SCHEMAS[self.kind][1]:
            if spec.kind != 'image' or not data.get(spec.key):
                continue
            upload = data[spec.key]
            if upload.size > 5 * 1024 * 1024:
                self.add_error(spec.key, '图样大小不能超过 5 MB。')
            elif upload.image.format not in ('PNG', 'JPEG'):
                self.add_error(spec.key, '请使用 PNG 或 JPEG 图片。')
            elif upload.image.width * upload.image.height > 20_000_000:
                self.add_error(spec.key, '图片尺寸过大，请缩小后上传。')

        if data.get('valid_from') and data.get('valid_until') and data['valid_until'] < data['valid_from']:
            self.add_error('valid_until', '截止日期不能早于起始日期。')

        # 不适用的授权项清空，防止先填写“有”后改为“无使用”仍带出旧值。
        if self.kind == 'trade_name':
            if data.get('has_name') == '否':
                for key in ('trade_name', 'is_registered', 'authorized_subsidiaries', 'investee_uses', 'investee_names'):
                    data[key] = ''
            elif data.get('investee_uses') == '否':
                data['investee_names'] = ''
        if self.kind == 'sinomach':
            for use, text, img, auth in [
                ('goods', 'goods_mark', 'goods_image', 'goods_authorized'),
                ('services', 'services_mark', 'services_image', 'services_authorized'),
            ]:
                if data.get(use) == '无':
                    data[text] = data[auth] = ''
                    data[img] = None
                    data[f'{img}_clear'] = True
            if data.get('name_use') == '无':
                data['name_authorized'] = ''

        if not self.complete:
            return data

        required = []
        if self.kind in MARK_KINDS:
            if not data.get('mark_text') and not self.has_image('mark_image', data):
                self.add_error('mark_text', '请填写商标标识文字或图样。')
            required += ['is_main', 'paid', 'litigation']
            if self.kind.startswith('unregistered'):
                required += ['use_type']
            else:
                required += ['registration_class', 'registration_number', 'valid_from', 'valid_until', 'holder_name', 'holder_level']
                if self.kind == 'overseas':
                    required += ['country']
        elif self.kind == 'trade_name':
            required += ['has_name', 'violations']
            if data.get('has_name') == '是':
                required += ['trade_name', 'is_registered', 'authorized_subsidiaries', 'investee_uses']
                if data.get('investee_uses') == '是':
                    required += ['investee_names']
        elif self.kind == 'sinomach':
            required += ['goods', 'services', 'name_use', 'investee_names']
            for use, text, img, auth in [
                ('goods', 'goods_mark', 'goods_image', 'goods_authorized'),
                ('services', 'services_mark', 'services_image', 'services_authorized'),
            ]:
                if data.get(use) and data[use] != '无':
                    required += [auth]
                    if not data.get(text) and not self.has_image(img, data):
                        self.add_error(text, '已填写使用事项，请填写相应标识文字或图样。')
            if data.get('name_use') and data['name_use'] != '无':
                required += ['name_authorized']
        for key in required:
            if not data.get(key) and key not in self.errors:
                self.add_error(key, '统一提交前请填写此项。')
        return data
