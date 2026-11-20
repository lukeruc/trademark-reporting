from contextlib import contextmanager
from datetime import date
from functools import wraps
import hashlib

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.shortcuts import render
from django.utils.crypto import constant_time_compare

from .credentials import USERNAME_MAX_LENGTH, account_version, enterprise_initials, random_password
from .forms import EntryForm
from .models import Enterprise, Entry, LoginThrottle, MarkImage, ReportingUnit, Submission, User
from .schema import EMPTY_ALLOWED_KINDS, ENTERPRISE_FIELDS, SCHEMAS, image_keys


class Conflict(Exception):
    pass


def conflict_page(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except Conflict as exc:
            return render(request, 'reporting/error.html', {'error': str(exc)}, status=409)
    return wrapper


def check_revision(unit, revision):
    if str(unit.revision) != str(revision):
        raise Conflict('数据或报送状态已经变化。请返回工作台刷新后，再进行操作。')


@contextmanager
def editable_unit(request):
    # 所有业务写入均先锁同一责任单位行，和提交/退回共用一把数据库锁。
    with transaction.atomic():
        unit = ReportingUnit.objects.select_for_update().get(user=request.user)
        if not unit.editable:
            raise Conflict('本单位已提交，数据已锁定。请请求管理员退回后再修改。')
        check_revision(unit, request.POST.get('revision'))
        yield unit


def touch(unit):
    unit.revision += 1
    unit.save(update_fields=['revision', 'updated_at'])


@transaction.atomic
def create_account(name, kind):
    base = enterprise_initials(name)
    password = random_password()
    index = 1
    while True:
        suffix = '' if index == 1 else str(index)
        username = base[:USERNAME_MAX_LENGTH - len(suffix)] + suffix
        if User.objects.filter(username=username).exists():
            index += 1
            continue
        try:
            # 唯一索引处理并发开户的竞争；保存点使重试不破坏外层事务。
            with transaction.atomic():
                user = User.objects.create_user(username=username, password=password)
            break
        except IntegrityError:
            if not User.objects.filter(username=username).exists():
                raise
            index += 1
    unit = ReportingUnit.objects.create(name=name, kind=kind, user=user)
    Enterprise.objects.create(unit=unit, name=name, level=2 if kind == 'hq' else 3, is_primary=True)
    return unit, password


@transaction.atomic
def reset_account(unit_id, action, version):
    unit = ReportingUnit.objects.select_for_update().get(pk=unit_id)
    user = User.objects.select_for_update().get(pk=unit.user_id)
    if user.is_manager or action not in ('username', 'password'):
        raise Conflict('无法重置此账号，请返回企业信息页。')
    if not constant_time_compare(account_version(user), str(version or '')):
        raise Conflict('账号信息已更新，请返回企业信息页刷新后再操作。')
    old_username = user.username
    password = None
    if action == 'password':
        password = random_password()
        user.set_password(password)
        user.save(update_fields=['password'])
    else:
        base = enterprise_initials(unit.name)
        index = 1
        while True:
            suffix = '' if index == 1 else str(index)
            username = base[:USERNAME_MAX_LENGTH - len(suffix)] + suffix
            occupied = User.objects.exclude(pk=user.pk).filter(username=username)
            if occupied.exists():
                index += 1
                continue
            if username == old_username:
                return unit, None, False
            try:
                with transaction.atomic():
                    user.username = username
                    user.save(update_fields=['username'])
                break
            except IntegrityError:
                if not occupied.exists():
                    raise
                index += 1
    keys = [hashlib.sha256(name.encode()).hexdigest() for name in {old_username, user.username}]
    LoginThrottle.objects.filter(key__in=keys).delete()
    unit.user = user
    return unit, password, True


def save_entry(form, enterprise, entry=None):
    data = {}
    for spec in SCHEMAS[form.kind][1]:
        if spec.key in ENTERPRISE_FIELDS:
            continue
        value = form.cleaned_data.get(spec.key)
        if spec.kind == 'image':
            if value:
                asset = MarkImage(unit=enterprise.unit, mime_type='image/png' if value.image.format == 'PNG' else 'image/jpeg')
                value.seek(0)
                asset.file.save('mark', value, save=True)
                data[spec.key] = str(asset.pk)
            elif not form.cleaned_data.get(f'{spec.key}_clear'):
                data[spec.key] = form.stored.get(spec.key, '')
        else:
            data[spec.key] = value.isoformat() if isinstance(value, date) else ('' if value is None else value)
    entry = entry or Entry(enterprise=enterprise, kind=form.kind)
    entry.data = data
    entry.save()
    if form.kind in enterprise.empty_sections:
        enterprise.empty_sections.remove(form.kind)
        enterprise.save(update_fields=['empty_sections'])
    return entry


def entry_data(entry):
    # 草稿始终使用所属企业的最新资料，兼容旧明细中单独填写的持有人字段。
    data = entry.data.copy()
    for spec in SCHEMAS[entry.kind][1]:
        if spec.key in ENTERPRISE_FIELDS:
            data[spec.key] = getattr(entry.enterprise, ENTERPRISE_FIELDS[spec.key])
    return data


def entry_errors(entry):
    # ImageField 不接收存储 UUID，它由 stored 独立校验。
    data = {k: v for k, v in entry.data.items() if k not in image_keys(entry.kind)}
    form = EntryForm(data=data, kind=entry.kind, stored=entry.data, complete=True)
    form.is_valid()
    return [
        f'{form.fields[key].label if key in form.fields else "本条记录"}：{message}'
        for key, values in form.errors.items() for message in values
    ]


def enterprise_errors(enterprise):
    problems = []
    if not enterprise.nature:
        problems.append({'text': '请填写企业性质。', 'edit_enterprise': True})
    entries = list(enterprise.entries.all())
    for kind in SCHEMAS:
        subset = [x for x in entries if x.kind == kind]
        if not subset and not (kind in EMPTY_ALLOWED_KINDS and kind in enterprise.empty_sections):
            text = '请填写明细，或确认“无”。' if kind in EMPTY_ALLOWED_KINDS else '请填写情况。'
            problems.append({'text': f'{SCHEMAS[kind][0]}：{text}', 'kind': kind})
        for entry in subset:
            for error in entry_errors(entry):
                problems.append({'text': f'{SCHEMAS[kind][0]}：{error}', 'kind': kind, 'entry_id': entry.id})
            for key in image_keys(kind):
                image_id = entry.data.get(key)
                if image_id and not MarkImage.objects.filter(pk=image_id, unit=enterprise.unit).exists():
                    problems.append({'text': f'{SCHEMAS[kind][0]}：标识图样不存在，请重新填写。', 'kind': kind, 'entry_id': entry.id})
    return problems


def unit_errors(unit):
    problems = []
    for enterprise in unit.enterprises.prefetch_related('entries'):
        for item in enterprise_errors(enterprise):
            problems.append({**item, 'enterprise': enterprise})
    return problems


def payload_for(unit):
    return {
        'schema_version': 1,
        'unit': {'id': unit.id, 'name': unit.name, 'kind': unit.kind},
        'enterprises': [
            {
                'id': enterprise.id, 'name': enterprise.name, 'level': enterprise.level,
                'nature': enterprise.nature, 'is_primary': enterprise.is_primary,
                'empty_sections': enterprise.empty_sections,
                'entries': [{'id': e.id, 'kind': e.kind, 'data': entry_data(e)} for e in enterprise.entries.all()],
            }
            for enterprise in unit.enterprises.prefetch_related('entries')
        ],
    }


@transaction.atomic
def submit(unit_id, revision):
    unit = ReportingUnit.objects.select_for_update().get(pk=unit_id)
    if unit.status == ReportingUnit.Status.SUBMITTED:
        return unit.current_submission, []  # 重复点击幂等
    check_revision(unit, revision)
    problems = unit_errors(unit)
    if problems:
        return None, problems
    number = (unit.submissions.aggregate(n=Max('version'))['n'] or 0) + 1
    payload = payload_for(unit)
    snapshot = Submission.objects.create(unit=unit, version=number, payload=payload)
    ids = [e['data'][key] for company in payload['enterprises'] for e in company['entries']
           for key in image_keys(e['kind']) if e['data'].get(key)]
    MarkImage.objects.filter(unit=unit, pk__in=ids).update(submitted=True)
    unit.status = ReportingUnit.Status.SUBMITTED
    unit.current_submission = snapshot
    unit.return_requested = False
    unit.return_request_note = ''
    unit.return_reason = ''
    unit.revision += 1
    unit.save()
    return snapshot, []


@transaction.atomic
def request_return(unit_id, revision, note):
    unit = ReportingUnit.objects.select_for_update().get(pk=unit_id)
    if unit.status != ReportingUnit.Status.SUBMITTED:
        raise Conflict('当前数据尚未提交或已被退回，请刷新页面。')
    if unit.return_requested:
        return
    check_revision(unit, revision)
    unit.return_requested, unit.return_request_note = True, note
    unit.revision += 1
    unit.save()


@transaction.atomic
def return_submission(unit_id, submission_id, reason):
    unit = ReportingUnit.objects.select_for_update().get(pk=unit_id)
    if unit.status != ReportingUnit.Status.SUBMITTED or str(unit.current_submission_id) != str(submission_id):
        raise Conflict('报送状态或版本已变化，请刷新后再退回。')
    unit.status = ReportingUnit.Status.RETURNED
    unit.return_requested = False
    unit.return_reason = reason
    unit.revision += 1
    unit.save()


def aggregate_payloads():
    # 一条 SQL 同时读取状态和当前快照，避免并发退回导致半旧半新的汇总。
    return list(
        ReportingUnit.objects.filter(status=ReportingUnit.Status.SUBMITTED)
        .order_by('kind', 'id').values_list('current_submission__payload', flat=True)
    )


def present_entry(entry):
    kind, data = entry['kind'], entry['data']
    fields = []
    for spec in SCHEMAS[kind][1]:
        value = data.get(spec.key)
        if value not in ('', None):
            fields.append({'label': spec.label, 'value': value, 'is_image': spec.kind == 'image'})
    title = data.get('mark_text') or data.get('trade_name') or SCHEMAS[kind][0]
    return {'id': entry.get('id'), 'title': title, 'fields': fields, 'kind': kind}


def present_payload(payload):
    result = {'unit': payload['unit'], 'enterprises': []}
    for company in payload['enterprises']:
        groups = []
        for kind, (label, _) in SCHEMAS.items():
            groups.append({
                'kind': kind, 'label': label,
                'empty': kind in company['empty_sections'],
                'entries': [present_entry(e) for e in company['entries'] if e['kind'] == kind],
            })
        result['enterprises'].append({**company, 'groups': groups})
    return result
