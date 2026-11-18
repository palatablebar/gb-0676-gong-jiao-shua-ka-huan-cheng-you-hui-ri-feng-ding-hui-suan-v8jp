from flask import render_template
from app.config import MODULES,RECEIPTS_MODULE,TITLE,SHORT
from app.pipeline import execute,load_sample
from app.errors import DomainError
from app import store

CHOICES=[['event_id','card','route','event_time','result','reported'],['event_id','card','route','line_group'],['event_id','card','fare_version','base_fare','reported','at'],['journey','card','route','business_day','at'],['event_id','card','route','base_fare','transfer_anchor','transfer_discount','cap_discount','expected','day_paid'],['event_id','card','expected','reported','difference','status'],['event_id','card','expected','reported','status'],['event_id','card','route','base_fare','transfer_discount','cap_discount','expected','accounted','action','amount','receipt_status']]
LABELS={'event_id':'事件','card':'卡号','route':'线路','event_time':'刷卡时间','result':'刷卡状态','reported':'实扣 / 元','line_group':'线路组','fare_version':'票价版','base_fare':'基础票价','at':'事件时间 UTC','journey':'行程编号','business_day':'业务日','transfer_anchor':'锚点','transfer_discount':'换乘优惠','cap_discount':'封顶减免','expected':'应扣 / 元','day_paid':'日累计','difference':'差额 / 元','status':'结论','accounted':'已确认到账 / 元','action':'处理','amount':'补扣/退款 / 元','receipt_status':'回执状态','receipt_ids':'回执号','note':'说明','accepted':'采纳行','versions':'版本','daily_caps':'卡日数量','transfer_hits':'换乘次数','input':'输入行','quarantine':'隔离行','duplicate':'重复行','blank':'空白行','repeated_headers':'重复表头','encoding':'编码','routes':'线路数','cards':'卡数','journeys':'行程数','records':'记录数','source_lines':'来源行数','sha256':'摘要','ready':'可归档','differences':'差异项','accepted_inputs':'采纳来源行','quarantine_inputs':'隔离来源行','output_records':'结果记录','excluded':'排除行','balanced':'行数平衡','expected_total':'预期合计','reported_total':'申报合计','confirmed':'已确认行程','pending':'待回执行程','unmatched':'无主回执','conflicts':'回执冲突','late_ignored':'终结后回执','duplicate_receipts':'重复回执','receipts':'回执条数','accounted_total':'已到账合计','settled_accounted':'已确认到账合计','pending_received':'待回执已到账','discount_total':'减免合计','charge_count':'补扣笔数','charge_total':'补扣合计','refund_count':'退款笔数','refund_total':'退款合计','unchanged':'不变','seal_ready':'可封存','benefit_policy':'权益口径','benefit_policy_label':'权益口径说明','resolved':'已裁决回执','set_sha256':'回执集合摘要','generated_at':'生成时刻'}

def _recon(run_id):
    try: return store.recon_latest(run_id)
    except DomainError: return None

def register_pages(app):
    def page(index):
        run=store.latest() or execute(load_sample("dirty.csv"))
        stage=run["stages"][index]
        recon=_recon(run["run_id"])
        return render_template(f"m{index+1}.html",title=TITLE,short=SHORT,modules=MODULES,receipts_module=RECEIPTS_MODULE,index=index,run=run,stage=stage,rows=stage["rows"],columns=CHOICES[index][:12],history=store.history(),labels=LABELS,recon=recon,seal=store.seal_status(run["run_id"]))
    def receipts_page():
        run=store.latest() or execute(load_sample("dirty.csv"))
        recon=_recon(run["run_id"])
        resolutions={r["receipt_id"]:r for r in store.resolutions_for(run["run_id"])}
        return render_template("m8.html",title=TITLE,short=SHORT,modules=MODULES,receipts_module=RECEIPTS_MODULE,index=len(MODULES),run=run,receipts=store.receipts_for(run["run_id"]),conflicts=store.conflicts_for(run["run_id"]),resolutions=resolutions,receipts_issues=(recon["receipt_quarantine"] if recon else [])+store.conflicts_for(run["run_id"]),recon=recon,versions=store.recon_versions(run["run_id"]),errors=store.recon_errors(run["run_id"]),seal=store.seal_status(run["run_id"]),documents=store.raw_documents_for(run["run_id"]),rows=recon["rows"] if recon else [],columns=CHOICES[7][:12],labels=LABELS)
    app.add_url_rule("/",view_func=lambda:page(4))
    for index,(slug,name) in enumerate(MODULES):
        app.add_url_rule("/"+slug,endpoint="page_"+slug,view_func=lambda index=index:page(index))
    app.add_url_rule("/receipts",endpoint="page_receipts",view_func=receipts_page)
