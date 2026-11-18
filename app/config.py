from pathlib import Path
import os
ROOT=Path(__file__).resolve().parent.parent
DATA_DIR=Path(os.environ.get("DATA_DIR", "/tmp/transitcap"))
TITLE='公交刷卡换乘优惠与日封顶回算'
SHORT='transitcap'
COLUMNS=['event_id', 'card', 'route', 'event_time', 'result', 'reported', 'note']
RECEIPT_COLUMNS=['receipt_id', 'event_id', 'card', 'amount', 'received_at', 'final']
RECEIPT_REQUIRED=['receipt_id', 'event_id', 'amount', 'received_at']
MODULES=[('taps', '刷卡采信'), ('routes', '线路换乘'), ('fares', '票价版本'), ('journeys', '行程配对'), ('discounts', '优惠回算'), ('clearing', '清分对账'), ('settlement', '日结归档')]
RECEIPTS_MODULE=('receipts','回执对账')
RULE_VERSION="2026.11.17-r1"
RECON_RULE_VERSION="2026.11.18-r1"
# 回执未齐时的裁决：confirmed = 只让已确认（含最终回执）的扣款参与换乘与封顶计算。
# 另一候选 entitlement（未回执也先占权益）会让未落账行程提前占用封顶额度，已否决。
BENEFIT_POLICY="confirmed"
BENEFIT_POLICY_LABEL={"confirmed":"确认制：仅已确认扣款参与权益计算","entitlement":"应享制：未回执也先占权益"}
