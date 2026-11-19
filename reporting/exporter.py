"""从固定原模板复制导出，扩行时统一映射单元格、合并区域、行高及图片锚点。"""
from copy import copy
from io import BytesIO
from math import ceil
import unicodedata

from django.conf import settings
from openpyxl import load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.drawing.image import Image
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment
from openpyxl.utils import get_column_letter
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.worksheet.cell_range import MultiCellRange

from .models import MarkImage

MAX_ROW_HEIGHT = 409
TEXT_LINE_HEIGHT = 20


def expand_sections(ws, regions):
    # region=(原始首行, 原始末行, 实际所需行数)，保留原有空白行，只在不足时扩展。
    growth = [(start, end, max(0, size - (end - start + 1))) for start, end, size in regions]

    def row_map(row):
        return row + sum(extra for _, end, extra in growth if row > end)

    merged = [copy(x) for x in ws.merged_cells.ranges]
    styles = {
        start: [(copy(ws.cell(start, col)._style), copy(ws.cell(start, col).alignment))
                for col in range(1, ws.max_column + 1)]
        for start, _, _ in regions
    }
    dimensions = {row: copy(dim) for row, dim in ws.row_dimensions.items()}
    cells = {}
    for (row, col), cell in list(ws._cells.items()):
        if isinstance(cell, MergedCell):
            continue
        cloned = copy(cell)
        cloned.row = row_map(row)
        cells[(cloned.row, col)] = cloned
    ws._cells = cells
    ws.merged_cells = MultiCellRange()
    ws.row_dimensions.clear()
    for row, dim in dimensions.items():
        dim.index = row_map(row)
        ws.row_dimensions[dim.index] = dim
    for region in merged:
        ws.merge_cells(start_row=row_map(region.min_row), end_row=row_map(region.max_row),
                       start_column=region.min_col, end_column=region.max_col)
    for start, end, extra in growth:
        for row in range(row_map(end) + 1, row_map(end) + extra + 1):
            for col, (style, alignment) in enumerate(styles[start], 1):
                ws.cell(row, col)._style = copy(style)
                ws.cell(row, col).alignment = copy(alignment)
            ws.row_dimensions[row].height = dimensions.get(start).height if start in dimensions else 30
    for img in ws._images:
        # 原模板中的图片位于表格下方，也随前方扩行下移。
        if hasattr(img.anchor, '_from'):
            img.anchor._from.row = row_map(img.anchor._from.row + 1) - 1
            if hasattr(img.anchor, 'to'):
                img.anchor.to.row = row_map(img.anchor.to.row + 1) - 1
    return row_map


def safe_value(cell, value):
    cell.value = value
    if isinstance(value, str):
        # 注册号保留前导零；以 = + - @ 开头的用户文本永远不是 Excel 公式。
        cell.data_type = 's'
        cell.number_format = '@'
    cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)


def estimated_lines(text, width):
    total = 0
    for line in str(text or '').split('\n'):
        length = sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in line)
        total += max(1, ceil(length / max(4, width - 1)))
    return total


def prepare_mark(asset, col_width):
    stream = BytesIO()
    with asset.file.open('rb') as source:
        stream.write(source.read())
    stream.seek(0)
    img = Image(stream)
    max_width = min(145, max(60, col_width * 7 - 12))
    ratio = min(max_width / img.width, 78 / img.height, 1)
    img.width, img.height = max(1, round(img.width * ratio)), max(1, round(img.height * ratio))
    return img


def prepare_rows(ws, records, make_values, image_columns, assets):
    rows = []
    for offset, record in enumerate(records):
        values = [offset + 1] + make_values(record)
        height = 40  # 像素；对应最小 30pt 行高。
        images = []
        for col, value in enumerate(values, 1):
            width = ws.column_dimensions[get_column_letter(col)].width
            height = max(height, estimated_lines(value, width) * TEXT_LINE_HEIGHT + 8)
        for key, col in image_columns.items():
            if record['data'].get(key):
                width = ws.column_dimensions[get_column_letter(col)].width
                img = prepare_mark(assets[record['data'][key]], width)
                text = values[col - 1]
                top = estimated_lines(text, width) * TEXT_LINE_HEIGHT + 4 if text else 4
                images.append({'col': col, 'img': img, 'top': top})
                height = max(height, top + img.height + 6)
        height *= .75  # Excel 使用 pt，图样锚点使用像素。
        rows.append({'values': values, 'height': height,
                     'span': max(1, ceil(height / MAX_ROW_HEIGHT)), 'images': images})
    return rows


def physical_rows(rows):
    return sum(row['span'] for row in rows)


def add_mark(ws, row, span, col, img, top):
    # 长记录跨多个物理行；把图样锚定到其实际所在行，而非使用越界偏移。
    anchor_row = row
    while anchor_row < row + span - 1:
        height = ws.row_dimensions[anchor_row].height / .75
        if top < height:
            break
        top -= height
        anchor_row += 1
    img.anchor = OneCellAnchor(
        _from=AnchorMarker(col=col - 1, row=anchor_row - 1,
                           colOff=pixels_to_EMU(4), rowOff=pixels_to_EMU(top)),
        ext=XDRPositiveSize2D(pixels_to_EMU(img.width), pixels_to_EMU(img.height)),
    )
    ws.add_image(img)


def write_rows(ws, start, records):
    row = start
    for record in records:
        span = record['span']
        for current in range(row, row + span):
            ws.row_dimensions[current].height = record['height'] / span
        for col, value in enumerate(record['values'], 1):
            if span > 1:
                # 各列纵向合并，仍是一条业务记录，不重复序号或商标件数。
                ws.merge_cells(start_row=row, end_row=row + span - 1,
                               start_column=col, end_column=col)
            safe_value(ws.cell(row, col), value)
        for image in record['images']:
            add_mark(ws, row, span, **image)
        row += span


def set_widths(ws, values):
    for col, width in enumerate(values, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0


def term(data):
    return f'{data.get("valid_from", "")} 至 {data.get("valid_until", "")}'


def registered(record):
    d = record['data']
    return [d.get('mark_text', ''), d.get('registration_class', ''), d.get('registration_number', ''),
            term(d), d.get('holder_name', ''), d.get('holder_level', ''),
            d.get('is_main', ''), d.get('licensees', ''), d.get('paid', ''), d.get('litigation', ''), d.get('remarks', '')]


def unregistered(record):
    d = record['data']
    return [d.get(k, '') for k in ('mark_text', 'is_main', 'use_type', 'licensees', 'paid', 'litigation', 'remarks')]


def trade_name(record):
    d = record['data']
    return [record['enterprise']['name']] + [
        d.get(k, '') for k in ('has_name', 'trade_name', 'is_registered', 'authorized_subsidiaries',
                               'investee_uses', 'investee_names', 'violations', 'remarks')
    ]


def sinomach(record):
    d, e = record['data'], record['enterprise']
    return [e['name'], f'{e["level"]}级，{e["nature"]}'] + [
        d.get(k, '') for k in ('goods', 'goods_mark', 'goods_authorized', 'services', 'services_mark',
                               'services_authorized', 'name_use', 'name_authorized', 'investee_names', 'remarks')
    ]


def export_workbook(payloads):
    wb = load_workbook(settings.TEMPLATE_WORKBOOK)
    records = []
    for payload in payloads:
        for company in payload['enterprises']:
            records.extend({**entry, 'enterprise': company} for entry in company['entries'])
            for kind, data in (
                ('trade_name', {'has_name': '否', 'violations': '无'}),
                ('sinomach', dict.fromkeys(('goods', 'services', 'name_use', 'investee_names'), '无')),
            ):
                if (kind in company.get('empty_sections', [])
                        and not any(entry['kind'] == kind for entry in company['entries'])):
                    # 企业的“无”确认仍须导出；不适用的明细和授权项留空。
                    records.append({'kind': kind, 'enterprise': company, 'data': data})
    ids = {value for entry in records for key, value in entry['data'].items()
           if key.endswith('image') and value}
    assets = {str(asset.id): asset for asset in MarkImage.objects.filter(id__in=ids)}
    if ids - assets.keys():
        raise ValueError('提交版本中的标识图样缺失，请恢复图样文件后再导出。')

    def select(kind):
        return [entry for entry in records if entry['kind'] == kind]

    # 第 1 张不作任何修改。
    ws = wb.worksheets[1]
    famous, other = select('domestic_famous'), select('domestic_other')
    set_widths(ws, [7, 24, 15, 22, 27, 32, 13, 12, 32, 12, 12, 36])
    famous_rows = prepare_rows(ws, famous, registered, {'mark_image': 2}, assets)
    other_rows = prepare_rows(ws, other, registered, {'mark_image': 2}, assets)
    mapping = expand_sections(ws, [(7, 9, physical_rows(famous_rows)), (13, 15, physical_rows(other_rows))])
    safe_value(ws['A3'], f'国内注册商标(总件数）：{len(famous) + len(other)}')
    safe_value(ws['A4'], f'驰名商标（件数）：{len(famous)}')
    safe_value(ws.cell(mapping(10), 1), f'其它注册商标（件数）：{len(other)}')
    write_rows(ws, mapping(7), famous_rows)
    write_rows(ws, mapping(13), other_rows)

    ws = wb.worksheets[2]
    overseas = select('overseas')
    set_widths(ws, [7, 20, 24, 15, 22, 27, 32, 13, 12, 32, 12, 12, 36])
    overseas_rows = prepare_rows(ws, overseas, lambda e: [e['data'].get('country', '')] + registered(e),
                                 {'mark_image': 3}, assets)
    mapping = expand_sections(ws, [(6, 12, physical_rows(overseas_rows))])
    safe_value(ws['A3'], f'境外注册商标(总件数）：{len(overseas)}')
    write_rows(ws, mapping(6), overseas_rows)

    ws = wb.worksheets[3]
    domestic, abroad = select('unregistered_domestic'), select('unregistered_overseas')
    set_widths(ws, [7, 28, 16, 20, 38, 14, 14, 40])
    domestic_rows = prepare_rows(ws, domestic, unregistered, {'mark_image': 2}, assets)
    abroad_rows = prepare_rows(ws, abroad, unregistered, {'mark_image': 2}, assets)
    mapping = expand_sections(ws, [(6, 9, physical_rows(domestic_rows)), (13, 16, physical_rows(abroad_rows))])
    safe_value(ws['A3'], f'境内未注册商标(总件数）：{len(domestic)}')
    safe_value(ws.cell(mapping(10), 1), f'境外未注册商标(总件数）：{len(abroad)}')
    write_rows(ws, mapping(6), domestic_rows)
    write_rows(ws, mapping(13), abroad_rows)

    ws = wb.worksheets[4]
    names = select('trade_name')
    groups = {level: [e for e in names if min(e['enterprise']['level'], 5) == level] for level in (2, 3, 4, 5)}
    ranges = [(5, 6, 2), (9, 10, 3), (13, 14, 4), (17, 18, 5)]
    set_widths(ws, [7, 40, 14, 28, 20, 24, 20, 40, 24, 40])
    group_rows = {level: prepare_rows(ws, records, trade_name, {}, assets) for level, records in groups.items()}
    mapping = expand_sections(ws, [(start, end, physical_rows(group_rows[level])) for start, end, level in ranges])
    for start, _, level in ranges:
        write_rows(ws, mapping(start), group_rows[level])

    ws = wb.worksheets[5]
    uses = select('sinomach')
    set_widths(ws, [7, 38, 22, 30, 24, 14, 30, 24, 14, 28, 14, 38, 40])
    use_rows = prepare_rows(ws, uses, sinomach, {'goods_image': 5, 'services_image': 8}, assets)
    mapping = expand_sections(ws, [(5, 11, physical_rows(use_rows))])
    write_rows(ws, mapping(5), use_rows)

    for ws in wb.worksheets[1:]:
        # 件数和分区名称原在 A 列，保持单行可向相邻空格延展，避免窄序号列换行。
        for row in ws:
            for cell in row:
                if cell.column == 1 and isinstance(cell.value, str) and '件数' in cell.value:
                    cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=False)
        ws.freeze_panes = 'C5'
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
