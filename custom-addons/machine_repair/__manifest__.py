{
    'name': '机台维修管理（机型模板 / 工单 / 包月）',
    'version': '20.0.1.3.0',
    'summary': '机型标准模板与配件分类、客户机台台账、工单抢单接力、库存联动、经验库、包月账单',
    'description': """
纺织机台维修业务管理
====================

阶段 1：机型标准模板（machine.base.template）与"专属电路板 / 通用小件"分类
阶段 2：客户机台台账（customer.machine）、模板复制、批量复制
阶段 3：扩展原生 repair.order —— 三级紧急度、抢单 / 退单 / 超时接力、看板高亮、每小时巡检
阶段 4：返修识别、完工后每天 3 次修改限制（管理员可解锁）、配件 SN 强校验
阶段 5：完工自动快照 repair.case（不可篡改）、双轨制经验库 repair.experience、每日聚合
阶段 6：包月账单按实际天数与放假天数折算、6 大报表现成（透视/图表，可导出 Excel）

说明
----
本模块以自定义模块实现，等价于方案中"用 Odoo Studio 建表"的做法。
社区版没有 Studio，而模块化实现可版本管理、可备份、升级 Odoo 时不被覆盖。
""",
    'author': 'Odoo local deployment',
    'license': 'LGPL-3',
    'category': 'Manufacturing/Repair',
    'depends': ['base', 'product', 'mail', 'stock', 'repair', 'account', 'board'],
    'data': [
        'security/groups.xml',
        'security/ir.access.csv',
        'data/product_category.xml',
        'data/board_qr_data.xml',
        'data/repair_cron.xml',
        'data/repair_case_data.xml',
        'data/repair_service_product.xml',
        'views/machine_base_template_views.xml',
        # the wizard action must exist before the form that references it via %(...)d
        'views/machine_copy_wizard_views.xml',
        'views/customer_machine_views.xml',
        'views/repair_order_views.xml',
        'views/repair_case_views.xml',
        'views/test_delete_wizard_views.xml',
        'views/monthly_billing_views.xml',
        'views/product_category_views.xml',
        'views/product_entry_views.xml',
        'views/board_qr_views.xml',
        'views/report_views.xml',
        'views/dashboard_views.xml',
        'views/mobile_templates.xml',
        'views/login_footer.xml',
        'views/menu.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
}
