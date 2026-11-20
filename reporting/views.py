from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from . import services
from .credentials import account_version
from .entrypoint import qr_image_path
from .forms import AccountDeleteForm, AccountForm, EnterpriseForm, EntryForm, LoginForm
from .models import Enterprise, Entry, EntryConfig, MarkImage, ReportingUnit, Submission
from .schema import EMPTY_ALLOWED_KINDS, SCHEMAS, SECTIONS, SECTION_BY_KIND, SINOMACH_NOTES


def role_required(manager=False):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if manager:
                if not request.user.is_manager:
                    raise PermissionDenied('此功能仅管理员可以使用。')
            elif request.user.is_manager or not ReportingUnit.objects.filter(user=request.user).exists():
                raise PermissionDenied('请使用企业填报账号进行此操作。')
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


manager_only = role_required(manager=True)
reporter_only = role_required()


class SignIn(LoginView):
    authentication_form = LoginForm
    template_name = 'registration/login.html'
    redirect_authenticated_user = True


@login_required
def home(request):
    return redirect('dashboard' if request.user.is_manager else 'workspace')


def company_url(company, kind=None):
    url = reverse('enterprise', args=[company.id])
    return url + '?' + urlencode({'section': SECTION_BY_KIND[kind]}) if kind else url


@reporter_only
@require_GET
def workspace(request):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    companies = []
    for company in unit.enterprises.prefetch_related('entries'):
        errors = services.enterprise_errors(company)
        companies.append({'company': company, 'issues': len(errors), 'entry_count': company.entries.count()})
    return render(request, 'reporting/workspace.html', {
        'unit': unit, 'companies': companies, 'history': unit.submissions.all(),
        'ready': bool(companies) and all(x['issues'] == 0 for x in companies),
    })


@reporter_only
@services.conflict_page
def enterprise_edit(request, pk=None):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    company = get_object_or_404(Enterprise, pk=pk, unit=unit) if pk else Enterprise(unit=unit, level=4)
    if not pk and unit.kind == 'hq':
        raise PermissionDenied('本部账号负责本部企业的信息。')
    if request.method == 'POST':
        with services.editable_unit(request) as unit:
            if pk:
                company = get_object_or_404(Enterprise, pk=pk, unit=unit)
            form = EnterpriseForm(request.POST, instance=company, unit=unit)
            if form.is_valid():
                form.save()
                services.touch(unit)
                messages.success(request, '企业信息已保存。')
                return redirect('enterprise', pk=company.pk)
    else:
        if not unit.editable:
            raise services.Conflict('数据已提交，退回后才能修改。')
        form = EnterpriseForm(instance=company, unit=unit)
    return render(request, 'reporting/form.html', {
        'form': form, 'title': '编辑企业信息' if pk else '添加下属企业',
        'description': '填写企业全称、实际产权级次和性质。', 'unit': unit,
        'back_url': reverse('workspace'), 'button': '保存企业信息',
    })


@reporter_only
@services.conflict_page
def enterprise_delete(request, pk):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    company = get_object_or_404(Enterprise, pk=pk, unit=unit, is_primary=False)
    if request.method == 'POST':
        with services.editable_unit(request) as unit:
            company = get_object_or_404(Enterprise, pk=pk, unit=unit, is_primary=False)
            company.delete()
            services.touch(unit)
        messages.success(request, '企业及其草稿明细已删除。')
        return redirect('workspace')
    return render(request, 'reporting/confirm.html', {
        'unit': unit, 'title': '删除企业', 'description': f'将删除“{company.name}”及其全部草稿明细。',
        'button': '确认删除', 'danger': True, 'back_url': company_url(company),
    })


@reporter_only
@require_GET
def enterprise_detail(request, pk):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    company = get_object_or_404(Enterprise, pk=pk, unit=unit)
    key = request.GET.get('section', 'domestic')
    section = next((s for s in SECTIONS if s['key'] == key), None)
    if not section:
        raise Http404
    groups = []
    for kind in section['kinds']:
        entries = list(company.entries.filter(kind=kind))
        groups.append({
            'kind': kind, 'label': SCHEMAS[kind][0], 'can_empty': kind in EMPTY_ALLOWED_KINDS,
            'empty': kind in company.empty_sections,
            'entries': [{**services.present_entry({'id': e.pk, 'kind': kind, 'data': services.entry_data(e)}),
                         'issues': services.entry_errors(e)} for e in entries],
        })
    return render(request, 'reporting/enterprise.html', {
        'unit': unit, 'company': company, 'sections': SECTIONS, 'section': section,
        'groups': groups, 'notes': SINOMACH_NOTES if key == 'sinomach' else [],
    })


@reporter_only
@require_POST
@services.conflict_page
def confirm_empty(request, pk, kind):
    if kind not in EMPTY_ALLOWED_KINDS:
        raise Http404
    with services.editable_unit(request) as unit:
        company = get_object_or_404(Enterprise, pk=pk, unit=unit)
        if company.entries.filter(kind=kind).exists():
            raise services.Conflict('本分类已有明细，请先删除误填明细，再确认“无”。')
        if request.POST.get('action') == 'clear':
            company.empty_sections = [x for x in company.empty_sections if x != kind]
        else:
            company.empty_sections = list(dict.fromkeys(company.empty_sections + [kind]))
        company.save(update_fields=['empty_sections'])
        services.touch(unit)
    messages.success(request, '已取消“无”确认，请填写相关情况。' if request.POST.get('action') == 'clear' else '已确认“无”。')
    return redirect(company_url(company, kind))


@reporter_only
@services.conflict_page
def entry_edit(request, pk, kind=None):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    entry = None
    if kind is None:
        entry = get_object_or_404(Entry.objects.select_related('enterprise'), pk=pk, enterprise__unit=unit)
        company, kind = entry.enterprise, entry.kind
    else:
        if kind not in SCHEMAS:
            raise Http404
        company = get_object_or_404(Enterprise, pk=pk, unit=unit)
    stored = entry.data if entry else {}
    if request.method == 'POST':
        with services.editable_unit(request) as unit:
            company = get_object_or_404(Enterprise, pk=company.pk, unit=unit)
            if entry:
                entry = get_object_or_404(Entry, pk=entry.pk, enterprise=company)
                stored = entry.data
            form = EntryForm(request.POST, request.FILES, kind=kind, stored=stored)
            if form.is_valid():
                services.save_entry(form, company, entry)
                services.touch(unit)
                messages.success(request, '草稿已保存，可继续填写。')
                return redirect(company_url(company, kind))
    else:
        if not unit.editable:
            raise services.Conflict('数据已提交，退回后才能修改。')
        form = EntryForm(kind=kind, stored=stored)
    current_images = [
        {'label': form.fields[k].label, 'id': stored[k]} for k in stored
        if k in form.fields and k.endswith('image') and stored[k]
    ]
    return render(request, 'reporting/form.html', {
        'form': form, 'unit': unit, 'company': company, 'title': SCHEMAS[kind][0],
        'description': company.name,
        'back_url': company_url(company, kind), 'button': '保存草稿', 'current_images': current_images,
        'multipart': True, 'notes': SINOMACH_NOTES if kind == 'sinomach' else [],
    })


@reporter_only
@services.conflict_page
def entry_delete(request, pk):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    entry = get_object_or_404(Entry.objects.select_related('enterprise'), pk=pk, enterprise__unit=unit)
    company, kind = entry.enterprise, entry.kind
    if request.method == 'POST':
        with services.editable_unit(request) as unit:
            entry = get_object_or_404(Entry, pk=pk, enterprise__unit=unit)
            entry.delete()
            services.touch(unit)
        messages.success(request, '明细已删除。没有相关情况时，请确认“无”。' if kind in EMPTY_ALLOWED_KINDS else '明细已删除，请重新填写企业字号情况。')
        return redirect(company_url(company, kind))
    return render(request, 'reporting/confirm.html', {
        'unit': unit, 'title': '删除明细', 'description': f'删除“{company.name}”的一条{SCHEMAS[kind][0]}记录。',
        'button': '确认删除', 'danger': True, 'back_url': company_url(company, kind),
    })


@reporter_only
@services.conflict_page
def submit_report(request):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    if request.method == 'POST':
        snapshot, issues = services.submit(unit.id, request.POST.get('revision'))
        if snapshot:
            messages.success(request, f'第 {snapshot.version} 版已统一提交，数据已锁定。')
            return redirect('workspace')
    else:
        if not unit.editable:
            return redirect('workspace')
        issues = services.unit_errors(unit)
    for issue in issues:
        issue['fill_url'] = company_url(issue['enterprise'], issue.get('kind'))
    return render(request, 'reporting/submit.html', {
        'unit': unit, 'issues': issues, 'enterprise_count': unit.enterprises.count(),
        'entry_count': Entry.objects.filter(enterprise__unit=unit).count(),
    }, status=422 if request.method == 'POST' and issues else 200)


@reporter_only
@services.conflict_page
def request_return_view(request):
    unit = get_object_or_404(ReportingUnit, user=request.user)
    if request.method == 'POST':
        note = request.POST.get('note', '')[:20000]
        services.request_return(unit.id, request.POST.get('revision'), note)
        messages.success(request, '退回请求已提交，管理员退回后即可修改。')
        return redirect('workspace')
    if unit.status != 'submitted':
        return redirect('workspace')
    return render(request, 'reporting/confirm.html', {
        'unit': unit, 'title': '请求管理员退回', 'description': '请求发出后，本批数据继续保持锁定，等待管理员处理。',
        'button': '发送退回请求', 'note_label': '需要修改的内容（可选）', 'back_url': reverse('workspace'),
    })


@login_required
@require_GET
def submission_detail(request, pk):
    query = Submission.objects.select_related('unit')
    if not request.user.is_manager:
        query = query.filter(unit__user=request.user)
    snapshot = get_object_or_404(query, pk=pk)
    return render(request, 'reporting/submission.html', {
        'snapshot': snapshot, 'unit': snapshot.unit,
        'report': services.present_payload(snapshot.payload), 'notes': SINOMACH_NOTES,
    })


@login_required
@require_GET
def mark_image(request, pk):
    query = MarkImage.objects.all()
    if request.user.is_manager:
        query = query.filter(submitted=True)
    else:
        query = query.filter(unit__user=request.user)
    asset = get_object_or_404(query, pk=pk)
    try:
        response = FileResponse(asset.file.open('rb'), content_type=asset.mime_type)
    except FileNotFoundError:
        raise Http404
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@manager_only
@require_GET
def dashboard(request):
    units = list(ReportingUnit.objects.select_related('user', 'current_submission'))
    counts = {key: sum(u.status == key for u in units) for key in ('draft', 'submitted', 'returned')}
    return render(request, 'reporting/dashboard.html', {
        'units': units, 'counts': counts, 'total': len(units),
        'requests_count': sum(u.return_requested for u in units),
    })


@manager_only
def account_create(request):
    form = AccountForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            unit, password = services.create_account(**form.cleaned_data)
        except IntegrityError:
            form.add_error(None, '该单位或本部账号已经创建，请检查账号列表。')
        else:
            return render(request, 'reporting/credentials.html', {'unit': unit, 'password': password})
    return render(request, 'reporting/form.html', {
        'form': form, 'title': '创建企业填报账号',
        'description': '账号为企业全称的拼音首字母，初始密码为随机 8 位字母数字组合。',
        'button': '创建账号', 'back_url': reverse('dashboard'),
    })


@manager_only
@require_http_methods(['GET', 'POST'])
@services.conflict_page
def account_manage(request, pk):
    unit = get_object_or_404(ReportingUnit.objects.select_related('user'), pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        unit, password, changed = services.reset_account(unit.pk, action, request.POST.get('account_version'))
        if not changed:
            messages.info(request, '当前用户名已符合企业名称拼音首字母规则，无需更改。')
            return redirect('account_manage', pk=unit.pk)
        return render(request, 'reporting/credentials.html', {
            'unit': unit, 'password': password, 'is_reset': True,
            'title': '密码已重置' if action == 'password' else '用户名已重置',
        })
    return render(request, 'reporting/account.html', {
        'unit': unit, 'account_version': account_version(unit.user),
    })


@manager_only
@services.conflict_page
def return_report(request, pk):
    unit = get_object_or_404(ReportingUnit.objects.select_related('current_submission'), pk=pk)
    if request.method == 'POST':
        services.return_submission(unit.pk, request.POST.get('submission_id'), request.POST.get('note', '')[:20000])
        messages.success(request, f'已退回“{unit.name}”，重新提交前该单位暂不纳入汇总。')
        return redirect('dashboard')
    if unit.status != 'submitted':
        raise services.Conflict('当前单位未处于已提交状态。')
    return render(request, 'reporting/confirm.html', {
        'unit': unit, 'title': '退回整批数据', 'description': f'退回“{unit.name}”第 {unit.current_submission.version} 版，企业可修改后重新提交。',
        'button': '确认退回', 'note_label': '退回说明（可选）', 'back_url': reverse('dashboard'),
        'submission_id': unit.current_submission_id,
    })


@manager_only
@require_http_methods(['GET', 'POST'])
@services.conflict_page
def account_delete(request, pk):
    unit = get_object_or_404(ReportingUnit.objects.select_related('user'), pk=pk, user__is_superuser=False)
    form = AccountDeleteForm(request.POST if request.method == 'POST' else None, unit=unit)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        failed_files = services.delete_account(
            unit.pk, data['revision'], data['account_version'],
            data['confirm_name'], data['confirm_username'], data['confirm_delete'],
        )
        if failed_files:
            messages.warning(request, f'“{unit.name}”账号及填报数据已删除，但部分图样文件清理失败，请联系服务器维护人员。')
        else:
            messages.success(request, f'已永久删除“{unit.name}”的账号及全部关联数据。')
        return redirect('dashboard')
    return render(request, 'reporting/account_delete.html', {
        'unit': unit, 'form': form,
        'counts': {
            'enterprises': unit.enterprises.count(),
            'entries': Entry.objects.filter(enterprise__unit=unit).count(),
            'submissions': unit.submissions.count(),
            'images': unit.images.count(),
        },
    }, status=422 if request.method == 'POST' else 200)


@manager_only
@require_GET
def aggregate(request):
    payloads = services.aggregate_payloads()
    return render(request, 'reporting/aggregate.html', {
        'reports': [services.present_payload(p) for p in payloads],
        'unit_count': len(payloads), 'enterprise_count': sum(len(p['enterprises']) for p in payloads),
        'notes': SINOMACH_NOTES,
    })


@manager_only
@require_GET
def export_report(request):
    from .exporter import export_workbook
    with transaction.atomic():
        output = export_workbook(services.aggregate_payloads(lock=True))
    return FileResponse(output, as_attachment=True,
                        filename=f'2026年商标字号统计表_{timezone.localdate():%Y%m%d}.xlsx',
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@manager_only
@require_GET
def entry_config(request):
    config = get_object_or_404(EntryConfig, pk=1)
    return render(request, 'reporting/entry_config.html', {'entry_url': config.url})


@manager_only
@require_GET
def entry_qr(request):
    config = get_object_or_404(EntryConfig, pk=1)
    try:
        image = qr_image_path(config.url).open('rb')
    except FileNotFoundError:
        raise Http404
    return FileResponse(image, content_type='image/png', filename='登录二维码.png')
