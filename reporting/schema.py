"""五张原表的字段定义；页面、提交校验和只读快照共用此口径。"""
from dataclasses import dataclass

YES_NO = ('是', '否')
HAS_NONE = ('有', '无')


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    kind: str = 'text'
    options: tuple = ()
    help: str = ''


def f(key, label, kind='text', options=(), help=''):
    return FieldSpec(key, label, kind, options, help)


MARK = [
    f('mark_text', '商标标识（含组合商标）', 'textarea', help='文字、图形或组合标识；文字和图样可任选一种，也可同时填写。'),
    f('mark_image', '商标标识图样', 'image'),
]
REGISTERED = MARK + [
    f('registration_class', '注册类别'),
    f('registration_number', '注册号', help='请填写完整注册号。'),
    f('valid_from', '注册效期：起始日期', 'date'),
    f('valid_until', '注册效期：截止日期', 'date'),
    f('holder_name', '注册商标持有人：企业名称'),
    f('holder_level', '注册商标持有人：企业产权级次', 'level'),
    f('is_main', '是否主商标', 'choice', YES_NO),
    f('licensees', '授权他人使用：被授权人名称（若有）', 'textarea', help='多个企业可分行填写。'),
    f('paid', '授权收费', 'choice', YES_NO),
    f('litigation', '是否涉诉', 'choice', YES_NO),
    f('remarks', '备注', 'textarea'),
]
UNREGISTERED = MARK + [
    f('is_main', '是否为主商标', 'choice', YES_NO),
    f('use_type', '使用类型', 'choice', ('商品商标', '服务商标')),
    f('licensees', '授权他人使用：被授权人名称（若有）', 'textarea'),
    f('paid', '授权收费', 'choice', YES_NO),
    f('litigation', '是否涉诉', 'choice', YES_NO),
    f('remarks', '备注', 'textarea'),
]
TRADE_NAME = [
    f('has_name', '是否有字号', 'choice', YES_NO),
    f('trade_name', '具体字号', help='名称中除区域、行业、企业性质以外的独立字段。'),
    f('is_registered', '字号是否为企业注册商标', 'choice', YES_NO),
    f('authorized_subsidiaries', '是否授权全资、控股企业使用', 'choice', YES_NO),
    f('investee_uses', '是否有参股企业使用', 'choice', YES_NO),
    f('investee_names', '使用字号的参股企业（全称）', 'textarea', help='多个企业可分行填写。'),
    f('violations', '其他违规授权或使用行为', 'choice', HAS_NONE),
    f('remarks', '备注', 'textarea'),
]
SINOMACH = [
    f('goods', '作为商品商标使用：商品名称', 'textarea', help='未使用时填写“无”。'),
    f('goods_mark', '使用的商标标识（商品）', 'textarea'),
    f('goods_image', '商标标识图样（商品）', 'image'),
    f('goods_authorized', '有无授权（商品）', 'choice', HAS_NONE),
    f('services', '作为服务商标使用：服务领域', 'textarea', help='未使用时填写“无”。'),
    f('services_mark', '使用的商标标识（服务）', 'textarea'),
    f('services_image', '商标标识图样（服务）', 'image'),
    f('services_authorized', '有无授权（服务）', 'choice', HAS_NONE),
    f('name_use', '作为企业字号使用', 'textarea', help='未使用时填写“无”。'),
    f('name_authorized', '有无授权（字号）', 'choice', HAS_NONE),
    f('investee_names', '下属参股企业作为字号使用（企业名称）', 'textarea', help='无此情形时填写“无”；多个企业可分行填写。'),
    f('remarks', '备注', 'textarea'),
]
SCHEMAS = {
    'domestic_famous': ('驰名商标', REGISTERED),
    'domestic_other': ('其它注册商标', REGISTERED),
    'overseas': ('境外注册商标（含港澳台）', [f('country', '注册地（国家或地区）')] + REGISTERED),
    'unregistered_domestic': ('境内未注册商标', UNREGISTERED),
    'unregistered_overseas': ('境外未注册商标', UNREGISTERED),
    'trade_name': ('企业字号', TRADE_NAME),
    'sinomach': ('国机商标（字号）使用情况', SINOMACH),
}
MARK_KINDS = tuple(list(SCHEMAS)[:5])
EMPTY_ALLOWED_KINDS = tuple(SCHEMAS)
SECTIONS = [
    {'key': 'domestic', 'number': '01', 'label': '境内注册商标', 'kinds': ['domestic_famous', 'domestic_other']},
    {'key': 'overseas', 'number': '02', 'label': '境外注册商标', 'kinds': ['overseas']},
    {'key': 'unregistered', 'number': '03', 'label': '未注册商标', 'kinds': ['unregistered_domestic', 'unregistered_overseas']},
    {'key': 'trade_name', 'number': '04', 'label': '企业字号', 'kinds': ['trade_name']},
    {'key': 'sinomach', 'number': '05', 'label': '国机商标及字号使用', 'kinds': ['sinomach']},
]
SECTION_BY_KIND = {kind: section['key'] for section in SECTIONS for kind in section['kinds']}
# 原表备注逐字保留，既用于在线页面，也用于说明字段口径。
SINOMACH_NOTES = [
    '1.本表所称的“国机商标”是指中国机械工业集团有限公司（集团公司）享有商标专用权的注册商标，国机商标标识包括“国机”、“SINOMACH”等文字、字母、图形及含上述要素的组合。',
    '2.商标使用授权，主要分为商标授权使用与品牌统一规划。授权使用的商标是指集团公司根据《中国机械工业集团有限公司国机商标授权许可管理办法》等规定，通过出具授权文件、签署《国机商标许可使用协议》等方式进行授权，主要用于商品商标和服务商标授权事项；品牌统一规划的商标使用授权，是指企业根据集团公司品牌建设的统一要求，在品牌规划范围内获得国机商标使用许可的情形。',
    '3.字号的使用授权，包括集团公司给予单独授权，以及新设公司时由集团公司决策机构（董事会、总经理办公会、党委常委会）会议审议，战略发展部下达批复，同意新设公司使用含“国机”字样的预名称等方式获得的使用许可。',
]


def image_keys(kind):
    return [field.key for field in SCHEMAS[kind][1] if field.kind == 'image']
