from decimal import Decimal
from app.values import number,instant,money
from app.errors import DomainError


def allocate(rows,params=None):
    """按卡日分配换乘减免与封顶减免。

    入参 rows 决定参与权益计算的行程集合；调用方在回执未齐时只传已确认行程，
    因此未落账行程不会提前占用换乘锚点或封顶额度。
    """
    params=params or {}
    cap=number(params.get("daily_cap","6.00"));off=number(params.get("transfer_discount","1.00"));window=number(params.get("transfer_minutes","60"))
    if cap<0 or off<0 or window<0:raise DomainError("BAD_DISCOUNT","优惠参数不能为负")
    spent={};anchor={};good=[]
    for row in sorted(rows,key=lambda r:(r["card"],instant(r["at"]),r["event_id"])):
        key=(row["card"],row["business_day"]);base=number(row["base_fare"]);previous=anchor.get(key);eligible=False
        if previous:
            delta=Decimal(str((instant(row["at"])-instant(previous["at"])).total_seconds()))/60
            eligible=previous["route"]!=row["route"] and Decimal(0)<=delta<window
        transfer=min(base,off) if eligible else Decimal(0)
        after_transfer=base-transfer;remaining=max(Decimal(0),cap-spent.get(key,Decimal(0)));charge=min(after_transfer,remaining)
        cap_discount=after_transfer-charge;spent[key]=spent.get(key,Decimal(0))+charge
        good.append({**row,"transfer_anchor":previous["event_id"] if eligible else "—","transfer_discount":money(transfer),"cap_discount":money(cap_discount),"expected":money(charge),"day_paid":money(spent[key])})
        if charge>0:anchor[key]=row
    return good,spent


def evaluate(rows,context):
    params=context.get("parameters",{})
    good,spent=allocate(rows,params)
    return {"rows":good,"quarantine":[],"metrics":{"expected_total":money(sum((number(r["expected"]) for r in good),Decimal(0))),"daily_caps":len(spent),"transfer_hits":sum(r["transfer_anchor"]!="—" for r in good)}}
