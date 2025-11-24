import os
from flask import Flask, request, jsonify, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from datetime import datetime, timedelta

app = Flask(__name__, static_folder='static', static_url_path='')

app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:///warehouse.db"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Drug(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    药品名称 = db.Column(db.String(128), nullable=False)
    批号 = db.Column(db.String(64), nullable=False)
    有效期至 = db.Column(db.Date, nullable=False)
    规格 = db.Column(db.String(64), nullable=False)
    货位 = db.Column(db.String(32), nullable=False)
    库存数量 = db.Column(db.Integer, nullable=False, default=0)
    状态 = db.Column(db.String(16), nullable=False, default='正常') # 正常, 即将过期, 已过期, 隔离

class Operation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    操作类型 = db.Column(db.String(16), nullable=False) # 入库, 出库, 转移, 隔离, 处理
    药品ID = db.Column(db.Integer, db.ForeignKey('drug.id'))
    操作数量 = db.Column(db.Integer, nullable=False)
    操作时间 = db.Column(db.DateTime, default=datetime.now)
    详情 = db.Column(db.String(256), nullable=True)

class Environment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    监测时间 = db.Column(db.DateTime, default=datetime.now)
    温度 = db.Column(db.Float)
    湿度 = db.Column(db.Float)
    备注 = db.Column(db.String(64), nullable=True)

def update_drug_status():
    today = datetime.now().date()
    warning = today + timedelta(days=30)
    for drug in Drug.query.filter(Drug.库存数量 > 0).all():
        if drug.有效期至 < today:
            drug.状态 = "已过期"
        elif drug.有效期至 < warning:
            drug.状态 = "即将过期"
        else:
            drug.状态 = "正常"
    db.session.commit()

@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

@app.route("/api/dashboard", methods=["GET"])
def dashboard():
    update_drug_status()
    total_stock = db.session.query(func.sum(Drug.库存数量)).scalar() or 0
    expiring = Drug.query.filter(Drug.状态 == "即将过期", Drug.库存数量 > 0).count()
    expired = Drug.query.filter(Drug.状态 == "已过期", Drug.库存数量 > 0).count()
    today = datetime.now().date()
    daily_ops = Operation.query.filter(func.date(Operation.操作时间) == today).count()
    return jsonify({
        "总库存": total_stock,
        "即将过期药品数": expiring,
        "已过期药品数": expired,
        "今日操作次数": daily_ops
    })

@app.route("/api/drug/list", methods=["GET"])
def list_drugs():
    name = request.args.get("药品名称")
    query = Drug.query
    if name:
        query = query.filter(Drug.药品名称.like(f"%{name}%"))
    drugs = query.all()
    return jsonify([{
        "药品ID": d.id,
        "药品名称": d.药品名称,
        "批号": d.批号,
        "规格": d.规格,
        "有效期至": d.有效期至.strftime("%Y-%m-%d"),
        "库存数量": d.库存数量,
        "货位": d.货位,
        "状态": d.状态
    } for d in drugs])

@app.route("/api/drug/<int:drug_id>", methods=["GET"])
def drug_detail(drug_id):
    d = Drug.query.get(drug_id)
    if not d:
        return jsonify({"错误提示": "未找到药品"}), 404
    return jsonify({
        "药品ID": d.id,
        "药品名称": d.药品名称,
        "批号": d.批号,
        "规格": d.规格,
        "有效期至": d.有效期至.strftime("%Y-%m-%d"),
        "库存数量": d.库存数量,
        "货位": d.货位,
        "状态": d.状态
    })

@app.route("/api/drug/storage/transfer", methods=["POST"])
def transfer_storage():
    data = request.json
    drug_id = data.get("药品ID")
    new_storage = data.get("新货位")
    if not drug_id or not new_storage:
        return jsonify({"错误提示": "缺少参数"}), 400
    if len(new_storage) > 32:
        return jsonify({"错误提示": "货位格式错误"}), 400
    drug = Drug.query.get(drug_id)
    if not drug:
        return jsonify({"错误提示": "未找到药品"}), 404
    old_storage = drug.货位
    drug.货位 = new_storage
    db.session.add(Operation(操作类型="转移", 药品ID=drug_id, 操作数量=0, 详情=f"由 {old_storage} 转移至 {new_storage}"))
    db.session.commit()
    return jsonify({"操作结果": "转移成功"})

@app.route("/api/drug/inbound", methods=["POST"])
def drug_inbound():
    data = request.json
    drug_name = data.get("药品名称")
    batch = data.get("批号")
    spec = data.get("规格")
    expire = data.get("有效期至")
    qty = int(data.get("数量") or 0)
    storage = data.get("货位")
    if not (drug_name and batch and spec and expire and qty > 0 and storage):
        return jsonify({"错误提示": "参数不完整"}), 400
    try:
        expire_dt = datetime.strptime(expire, "%Y-%m-%d").date()
    except Exception:
        return jsonify({"错误提示": "有效期格式错误"}), 400
    drug = Drug.query.filter_by(药品名称=drug_name, 批号=batch, 规格=spec, 有效期至=expire_dt, 货位=storage).first()
    if drug is None:
        drug = Drug(药品名称=drug_name, 批号=batch, 规格=spec, 有效期至=expire_dt, 库存数量=qty, 货位=storage)
        db.session.add(drug)
        db.session.commit()
    else:
        drug.库存数量 += qty
        db.session.commit()
    db.session.add(Operation(操作类型="入库", 药品ID=drug.id, 操作数量=qty, 详情="药品入库"))
    db.session.commit()
    update_drug_status()
    return jsonify({"操作结果": "入库成功"})

@app.route("/api/drug/outbound", methods=["POST"])
def drug_outbound():
    data = request.json
    drug_id = data.get("药品ID")
    qty = int(data.get("数量") or 0)
    if not drug_id or qty <= 0:
        return jsonify({"错误提示": "参数不完整"}), 400
    drug = Drug.query.get(drug_id)
    if not drug:
        return jsonify({"错误提示": "未找到药品"}), 404
    if drug.库存数量 < qty:
        return jsonify({"错误提示": "库存不足"}), 400
    drug.库存数量 -= qty
    db.session.add(Operation(操作类型="出库", 药品ID=drug_id, 操作数量=qty, 详情="药品出库"))
    db.session.commit()
    update_drug_status()
    return jsonify({"操作结果": "出库成功"})

@app.route("/api/drug/expiry/isolate", methods=["POST"])
def expiry_isolate():
    data = request.json
    drug_id = data.get("药品ID")
    if not drug_id:
        return jsonify({"错误提示": "参数不完整"}), 400
    drug = Drug.query.get(drug_id)
    if not drug:
        return jsonify({"错误提示": "未找到药品"}), 404
    if drug.状态 not in ("已过期", "即将过期"):
        return jsonify({"错误提示": "药品未到隔离条件"}), 400
    drug.状态 = "隔离"
    db.session.add(Operation(操作类型="隔离", 药品ID=drug_id, 操作数量=drug.库存数量, 详情="药品隔离"))
    db.session.commit()
    return jsonify({"操作结果": "隔离完成"})

@app.route("/api/drug/expiry/dispose", methods=["POST"])
def expiry_dispose():
    data = request.json
    drug_id = data.get("药品ID")
    if not drug_id:
        return jsonify({"错误提示": "参数不完整"}), 400
    drug = Drug.query.get(drug_id)
    if not drug:
        return jsonify({"错误提示": "未找到药品"}), 404
    if drug.状态 != "隔离":
        return jsonify({"错误提示": "药品未隔离，无法处理"}), 400
    qty_disposed = drug.库存数量
    drug.库存数量 = 0
    drug.状态 = "已过期"
    db.session.add(Operation(操作类型="处理", 药品ID=drug_id, 操作数量=qty_disposed, 详情="过期处理"))
    db.session.commit()
    return jsonify({"操作结果": "过期处理完成"})

@app.route("/api/environment/sync", methods=["POST"])
def env_sync():
    data = request.json
    try:
        temp = float(data.get("温度"))
        hum = float(data.get("湿度"))
    except Exception:
        return jsonify({"错误提示": "温湿度格式错误"}), 400
    e = Environment(温度=temp, 湿度=hum, 备注=data.get("备注", ""))
    db.session.add(e)
    db.session.commit()
    return jsonify({"操作结果": "环境数据同步成功"})

@app.route("/api/environment/latest", methods=["GET"])
def env_latest():
    e = Environment.query.order_by(Environment.监测时间.desc()).first()
    if not e:
        return jsonify({"错误提示": "暂无环境数据"}), 404
    return jsonify({
        "监测时间": e.监测时间.strftime("%Y-%m-%d %H:%M:%S"),
        "温度": e.温度,
        "湿度": e.湿度,
        "备注": e.备注
    })

@app.cli.command("initdb")
def initdb():
    with app.app_context():
        db.create_all()
    print("初始化数据库完成！")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5000, debug=True)