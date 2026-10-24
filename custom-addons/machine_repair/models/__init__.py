from . import ir_http
from . import product_category
from . import product_template
from . import machine_base_template
from . import board_qr
from . import board_photo
# 客户类型（包月用户 / 活期用户）挂在 res.partner 上，机台台账用 related 字段跟着走
from . import res_partner
from . import customer_machine
from . import machine_copy_wizard
# 器件基线「整列设置」向导
from . import machine_billing_column
# repair_case defines repair.fault.category, which repair.order and monthly reports reference
from . import repair_case
from . import monthly_billing
# repair_service_line adds the custom price used for out-of-warranty charging
from . import repair_service_line
from . import repair_video
from . import repair_order
# 发货单（一个快递单号可合并多张工单）与后台批量发货向导
from . import shipment
from . import repair_ship_wizard
from . import test_delete_wizard
# 手机端定位：工作位置、上报流水、最新位置快照、人员分配与设置
from . import geo
from . import work_location
from . import location_ping
from . import user_location
from . import res_users
from . import res_config_settings
