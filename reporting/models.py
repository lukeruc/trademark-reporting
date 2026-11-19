import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q


class User(AbstractUser):
    @property
    def is_manager(self):
        return self.is_superuser


class ReportingUnit(models.Model):
    class Kind(models.TextChoices):
        HEADQUARTERS = 'hq', '本部'
        SUBSIDIARY = 'subsidiary', '三级企业'

    class Status(models.TextChoices):
        DRAFT = 'draft', '草稿'
        SUBMITTED = 'submitted', '已提交'
        RETURNED = 'returned', '已退回'

    name = models.CharField('报送单位全称', max_length=300, unique=True)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    user = models.OneToOneField(User, on_delete=models.PROTECT, related_name='reporting_unit')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    revision = models.PositiveIntegerField(default=0)
    current_submission = models.ForeignKey('Submission', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    return_requested = models.BooleanField(default=False)
    return_request_note = models.TextField(blank=True)
    return_reason = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['kind', 'id']
        constraints = [models.UniqueConstraint(fields=['kind'], condition=Q(kind='hq'), name='one_headquarters')]

    @property
    def editable(self):
        return self.status != self.Status.SUBMITTED

    def __str__(self):
        return self.name


class Enterprise(models.Model):
    NATURES = [('全资', '全资'), ('控股', '控股'), ('实际控制', '实际控制')]
    unit = models.ForeignKey(ReportingUnit, on_delete=models.CASCADE, related_name='enterprises')
    name = models.CharField('企业名称（全称）', max_length=300)
    level = models.PositiveSmallIntegerField('企业产权级次')
    nature = models.CharField('企业性质', max_length=10, choices=NATURES, blank=True)
    is_primary = models.BooleanField(default=False)
    empty_sections = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ['-is_primary', 'level', 'id']
        constraints = [
            models.UniqueConstraint(fields=['unit', 'name'], name='enterprise_name_in_unit'),
            models.UniqueConstraint(fields=['unit'], condition=Q(is_primary=True), name='one_primary_in_unit'),
            models.CheckConstraint(condition=Q(level__gte=2), name='enterprise_level_at_least_two'),
        ]


class Entry(models.Model):
    enterprise = models.ForeignKey(Enterprise, on_delete=models.CASCADE, related_name='entries')
    kind = models.CharField(max_length=40)
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        constraints = [models.CheckConstraint(condition=Q(kind__in=[
            'domestic_famous', 'domestic_other', 'overseas', 'unregistered_domestic',
            'unregistered_overseas', 'trade_name', 'sinomach',
        ]), name='valid_entry_kind')]


class Submission(models.Model):
    unit = models.ForeignKey(ReportingUnit, on_delete=models.PROTECT, related_name='submissions')
    version = models.PositiveIntegerField()
    payload = models.JSONField()
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['unit', 'version'], name='unique_submission_version')]


def mark_path(instance, filename):
    # 由验证后的真实图片格式确定后缀；不使用用户文件名作为存储路径。
    suffix = '.png' if instance.mime_type == 'image/png' else '.jpg'
    return f'{instance.unit_id}/{instance.id.hex}{suffix}'


class MarkImage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unit = models.ForeignKey(ReportingUnit, on_delete=models.PROTECT, related_name='images')
    file = models.ImageField(upload_to=mark_path)
    mime_type = models.CharField(max_length=30)
    submitted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class EntryConfig(models.Model):
    # 固定 pk=1，启动时自动保存登录入口，管理员页面只读。
    url = models.URLField(max_length=300)
    updated_at = models.DateTimeField(auto_now=True)


class LoginThrottle(models.Model):
    key = models.CharField(primary_key=True, max_length=64)
    failures = models.PositiveIntegerField(default=0)
    since = models.DateTimeField()
