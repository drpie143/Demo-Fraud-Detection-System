import os
import sys
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
load_dotenv(project_root / ".env")

def load_processed_data():
    data_file = project_root / "evaluation" / "data" / "processed_seed_data.json"
    if not data_file.exists():
        print(f"❌ Không tìm thấy {data_file}. File này chưa được tạo!")
        print("   Vui lòng chạy `python evaluation/build_seed_data.py` trước.")
        sys.exit(1)
        
    with open(data_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        
    return data["profiles"], data["transactions"], data["raw_edges"]

# ─────────────────────────────────────────────────────────────
# PUSH: REDIS
# ─────────────────────────────────────────────────────────────

def push_redis(profiles: list[dict], raw_edges: list[dict]):
    print("\n" + "─" * 55)
    print("🔑 [1/4] REDIS CLOUD - Phase 1 Screening Data")
    print("─" * 55)

    from configs.settings import settings
    if settings.demo_mode:
        print("  SKIP: DEMO_MODE=true, using local simulator")
        return False
    if not settings.redis_password or settings.redis_host == "localhost":
        print("  ⏭️  SKIP: Chưa cấu hình REDIS_HOST/REDIS_PASSWORD")
        return False

    try:
        import redis
        r = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True,
            username=settings.redis_username,
            password=settings.redis_password,
        )
        r.ping()
        print(f"  ✅ Connected: {settings.redis_host}:{settings.redis_port}")
    except Exception as e:
        print(f"  ❌ Redis connection failed: {e}")
        return False

    # Clear old data efficiently
    for pattern in ["account:*", "whitelist:*", "risk_score:*",
                    "velocity:*", "rules:*", "txn:result:*"]:
        keys = r.keys(pattern)
        if keys:
            r.delete(*keys)
    r.delete("blacklist")
    print("  🗑️  Cleared old data")

    pipe = r.pipeline(transaction=False)

    # ─── Account Profiles ───
    for p in profiles:
        acc_id = p["customer_id"]
        pipe.hset(f"account:{acc_id}", mapping={
            "name": p["name"],
            "type": p["account_type"],
            "country": "VN",
            "status": "active" if p["risk_category"] != "critical" else "blocked",
        })
    print(f"  ✅ {len(profiles)} account profiles (batched)")

    # ─── Whitelist: low-risk senders with known receivers ───
    low_risk = [p for p in profiles if p["risk_category"] == "low" and p["avg_monthly_transactions"] > 0]
    
    # Pre-calculate receivers per sender
    recv_map = defaultdict(lambda: defaultdict(int))
    sender_counts = defaultdict(int)
    for edge in raw_edges:
        sender = edge["sender_account_no"]
        receiver = edge["receiver_account_no"]
        recv_map[sender][receiver] += 1
        sender_counts[sender] += 1
        
    wl_count = 0
    for p in low_risk[:30]:  # Top 30 low-risk accounts
        acc_id = p["customer_id"]
        receivers = sorted(recv_map[acc_id].items(), key=lambda x: x[1], reverse=True)[:3]
        if receivers:
            trust_map = {recv: str(90 - i*10) for i, (recv, _) in enumerate(receivers)}
            pipe.hset(f"whitelist:{acc_id}", mapping=trust_map)
            wl_count += 1
    print(f"  ✅ Whitelists for {wl_count} accounts (batched)")

    # ─── Blacklist: critical/high-risk accounts ───
    critical = [p for p in profiles if p["risk_category"] == "critical"]
    high_fraud = [p for p in profiles if p.get("fraud_ratio", 0) >= 0.8]
    blacklisted = set()
    for p in (critical + high_fraud):
        acc_id = p["customer_id"]
        pipe.sadd("blacklist", acc_id)
        pipe.hset(f"account:{acc_id}", mapping={
            "name": p["name"],
            "type": p["account_type"],
            "status": "blocked",
        })
        blacklisted.add(acc_id)
    print(f"  ✅ {len(blacklisted)} blacklisted accounts (batched)")

    # ─── Risk Scores ───
    risk_map = {"low": 0.1, "medium": 0.45, "high": 0.75, "critical": 0.95}
    now_iso = datetime.now().isoformat()
    for p in profiles:
        base = risk_map.get(p["risk_category"], 0.3)
        jitter = random.uniform(-0.05, 0.05)
        score = max(0.0, min(1.0, base + jitter))
        pipe.hset(f"risk_score:{p['customer_id']}", mapping={
            "score": str(round(score, 2)),
            "updated_at": now_iso,
        })
    print(f"  ✅ Risk scores for {len(profiles)} accounts (batched)")

    # ─── Velocity Counters (from CSV frequency) ───
    for acc_id, count in sender_counts.items():
        if count > 5:
            hourly_key = f"velocity:{acc_id}:hourly"
            for _ in range(min(count, 20)):
                pipe.incr(hourly_key)
            pipe.expire(hourly_key, 3600)
            daily_key = f"velocity:{acc_id}:daily"
            for _ in range(min(count, 20)):
                pipe.incr(daily_key)
            pipe.expire(daily_key, 86400)
    print(f"  ✅ Velocity counters for suspicious accounts (batched)")

    # ─── Screening Rules ───
    pipe.hset("rules:velocity", mapping={
        "max_transactions_per_hour": str(settings.suspicious_velocity_threshold),
        "max_transactions_per_day": "20",
        "max_amount_per_day": "250000000",
    })
    pipe.hset("rules:amount_threshold", mapping={
        "instant_allow_max": str(settings.instant_allow_max),
        "escalate_threshold": str(settings.elevated_amount_threshold),
        "instant_block_threshold": str(settings.large_amount_threshold),
        "currency": "VND",
    })
    
    # Execute batch
    pipe.execute()
    print(f"  ✅ Screening rules (batched pipeline executed)")

    total_keys = r.dbsize()
    print(f"\n  📊 TỔNG: {total_keys} keys in Redis")
    return True


# ─────────────────────────────────────────────────────────────
# PUSH: NEO4J
# ─────────────────────────────────────────────────────────────

def push_neo4j(profiles: list[dict], raw_edges: list[dict]):
    print("\n" + "─" * 55)
    print("📊 [2/4] NEO4J AURADB - Graph Data")
    print("─" * 55)

    from configs.settings import settings
    if settings.demo_mode:
        print("  SKIP: DEMO_MODE=true, using local simulator")
        return False
    if not settings.neo4j_password or settings.neo4j_password == "your-neo4j-password-here":
        print("  ⏭️  SKIP: Chưa cấu hình NEO4J_PASSWORD")
        return False

    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        driver.verify_connectivity()
        print(f"  ✅ Connected: {settings.neo4j_uri}")
    except Exception as e:
        print(f"  ❌ Neo4j connection failed: {e}")
        return False

    with driver.session() as session:
        # Clear
        session.run("MATCH (n) DETACH DELETE n")
        print("  🗑️  Cleared old data")

        # ─── Account Nodes ───
        session.run(
            "UNWIND $profiles AS p "
            "CREATE (:Account {id: p.customer_id, name: p.name, risk: p.risk_category, "
            "type: p.account_type, account_age_days: p.account_age_days})",
            profiles=profiles
        )
        print(f"  ✅ {len(profiles)} Account nodes")

        # ─── Device Nodes ───
        devices = {str(e["device_id"]) for e in raw_edges if e.get("device_id")}
        dev_list = [{"id": d, "label": f"Device-{d[:8]}"} for d in devices]
        session.run("UNWIND $devices AS d CREATE (:Device {id: d.id, label: d.label})", devices=dev_list)
        print(f"  ✅ {len(devices)} Device nodes")

        # ─── IP Nodes ───
        ips = {str(e["ip_address"]) for e in raw_edges if e.get("ip_address")}
        ip_list = []
        for ip in ips:
            is_vpn = any(kw in str(ip) for kw in ["185.220", "47.135", "tor", "vpn"])
            ip_list.append({"id": ip, "label": ip, "vpn": is_vpn})
        session.run("UNWIND $ips AS ip CREATE (:IP {id: ip.id, label: ip.label, is_vpn: ip.vpn})", ips=ip_list)
        print(f"  ✅ {len(ips)} IP nodes")

        # ─── Merchant Nodes (keep 2 for patterns) ───
        session.run("CREATE (:Merchant {id: 'MERCH_NORMAL', label: 'VinMart', is_shell: false})")
        session.run("CREATE (:Merchant {id: 'MERCH_SHELL', label: 'Shell Company Ltd', is_shell: true})")
        print(f"  ✅ 2 Merchant nodes")

        # ─── TRANSFERS_TO edges (aggregated) ───
        transfers_map = defaultdict(lambda: {"amount": 0.0, "count": 0})
        for edge in raw_edges:
            key = (edge["sender_account_no"], edge["receiver_account_no"])
            transfers_map[key]["amount"] += float(edge["amount"])
            transfers_map[key]["count"] += 1
            
        transfer_list = [{"src": src, "dst": dst, "amt": round(stats["amount"], 2), "cnt": stats["count"]} 
                         for (src, dst), stats in transfers_map.items()]
        session.run(
            "UNWIND $transfers AS t MATCH (a:Account {id: t.src}), (b:Account {id: t.dst}) "
            "CREATE (a)-[:TRANSFERS_TO {total_amount: t.amt, count: t.cnt}]->(b)", 
            transfers=transfer_list
        )
        print(f"  ✅ {len(transfers_map)} TRANSFERS_TO edges")

        # ─── USES_DEVICE edges ───
        device_edges = {(e["sender_account_no"], e["device_id"]) for e in raw_edges if e.get("device_id")}
        dev_edge_list = [{"acc": a, "dev": str(d)} for a, d in device_edges]
        session.run(
            "UNWIND $edges AS e MATCH (a:Account {id: e.acc}), (d:Device {id: e.dev}) "
            "MERGE (a)-[:USES_DEVICE {since: '2025-01'}]->(d)", 
            edges=dev_edge_list
        )
        print(f"  ✅ {len(device_edges)} USES_DEVICE edges")

        # ─── CONNECTS_FROM edges ───
        ip_edges = {(e["sender_account_no"], e["ip_address"]) for e in raw_edges if e.get("ip_address")}
        ip_edge_list = [{"acc": a, "ip": str(i)} for a, i in ip_edges]
        session.run(
            "UNWIND $edges AS e MATCH (a:Account {id: e.acc}), (ip:IP {id: e.ip}) "
            "MERGE (a)-[:CONNECTS_FROM {frequency: 'regular'}]->(ip)",
            edges=ip_edge_list
        )
        print(f"  ✅ {len(ip_edges)} CONNECTS_FROM edges")

    # Verify
    with driver.session() as session:
        count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        edges = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        print(f"\n  📊 TỔNG: {count} nodes, {edges} relationships")

    driver.close()
    return True


# ─────────────────────────────────────────────────────────────
# PUSH: MONGODB
# ─────────────────────────────────────────────────────────────

def push_mongodb(profiles: list[dict], transactions: list[dict]):
    print("\n" + "─" * 55)
    print("📦 [3/4] MONGODB ATLAS - Document Data")
    print("─" * 55)

    from configs.settings import settings
    if settings.demo_mode:
        print("  SKIP: DEMO_MODE=true, using local simulator")
        return False
    if not settings.mongodb_uri or "xxxxx" in settings.mongodb_uri:
        print("  ⏭️  SKIP: Chưa cấu hình MONGODB_URI")
        return False

    try:
        from pymongo import MongoClient
        client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        db = client[settings.mongodb_db_name]
        print(f"  ✅ Connected: {settings.mongodb_db_name}")
    except Exception as e:
        print(f"  ❌ MongoDB connection failed: {e}")
        return False

    # Profiles
    profiles_col = db["customer_profiles"]
    profiles_col.delete_many({})
    profiles_col.insert_many(profiles)
    print(f"  ✅ {len(profiles)} customer profiles")

    # Transactions
    txn_col = db["transaction_history"]
    txn_col.delete_many({})

    batch_size = 100
    for i in range(0, len(transactions), batch_size):
        batch = transactions[i:i+batch_size]
        txn_col.insert_many(batch)
    txn_col.create_index("account_id")
    print(f"  ✅ {len(transactions)} transaction records")

    client.close()
    return True


# ─────────────────────────────────────────────────────────────
# PUSH: CHROMADB (giữ nguyên fraud patterns)
# ─────────────────────────────────────────────────────────────

def push_chromadb():
    print("\n" + "─" * 55)
    print("🔍 [4/4] CHROMADB - Fraud Knowledge Base")
    print("─" * 55)

    try:
        import chromadb
    except Exception:
        print("  ⏭️  SKIP: ChromaDB không tương thích")
        return False

    from configs.settings import settings
    if settings.demo_mode:
        print("  SKIP: DEMO_MODE=true, using local simulator")
        return False
    if not settings.chroma_api_key:
        print("  ⏭️  SKIP: CHROMA_API_KEY chưa cấu hình")
        return False

    try:
        client = chromadb.HttpClient(
            host=settings.chroma_host,
            ssl=True,
            headers={"x-chroma-token": settings.chroma_api_key},
            tenant=settings.chroma_tenant,
            database=settings.chroma_database,
        )
        try:
            client.delete_collection(settings.chroma_collection_name)
        except Exception:
            pass
        collection = client.create_collection(
            name=settings.chroma_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"  ✅ Collection: {settings.chroma_collection_name}")
    except Exception as e:
        print(f"  ❌ ChromaDB error: {e}")
        return False

    # Import patterns from existing setup
    from evaluation.setup import CHROMA_DOCUMENTS

    collection.add(
        ids=[d["id"] for d in CHROMA_DOCUMENTS],
        documents=[d["text"] for d in CHROMA_DOCUMENTS],
        metadatas=[d["metadata"] for d in CHROMA_DOCUMENTS],
    )
    print(f"  ✅ {len(CHROMA_DOCUMENTS)} fraud pattern documents")
    return True


# ─────────────────────────────────────────────────────────────
# UPDATE SIMULATORS (in-memory fallback)
# ─────────────────────────────────────────────────────────────

def update_simulators(profiles: list[dict], transactions: list[dict], raw_edges: list[dict]):
    print("\n" + "─" * 55)
    print("💾 Updating In-Memory Simulators (fallback)")
    print("─" * 55)

    from infrastructure.databases.simulators import (
        redis_sim, dynamodb_sim, neptune_sim,
    )

    # Redis Simulator
    redis_sim._whitelist = set()
    low_risk = [p for p in profiles if p["risk_category"] == "low" and p["avg_monthly_transactions"] > 0]
    for p in low_risk[:20]:
        redis_sim._whitelist.add(p["customer_id"])

    redis_sim._blacklist = set()
    for p in profiles:
        if p["risk_category"] == "critical" or p.get("fraud_ratio", 0) >= 0.8:
            redis_sim._blacklist.add(p["customer_id"])

    risk_map = {"low": 0.1, "medium": 0.45, "high": 0.75, "critical": 0.95}
    redis_sim._risk_scores = {}
    for p in profiles:
        base = risk_map.get(p["risk_category"], 0.3)
        redis_sim._risk_scores[p["customer_id"]] = round(base + random.uniform(-0.05, 0.05), 2)

    redis_sim._velocity = {}
    sender_counts = defaultdict(int)
    for edge in raw_edges:
        sender_counts[edge["sender_account_no"]] += 1
        
    for acc_id, count in sender_counts.items():
        if count > 5:
            redis_sim._velocity[acc_id] = [
                (datetime.now() - timedelta(minutes=i*4)).isoformat()
                for i in range(min(count, 20))
            ]

    # DynamoDB Simulator
    dynamodb_sim._profiles = {}
    for p in profiles:
        dynamodb_sim._profiles[p["customer_id"]] = p.copy()

    dynamodb_sim._transactions = defaultdict(list)
    for txn in transactions:
        dynamodb_sim._transactions[txn["account_id"]].append(txn)

    # Neptune Simulator
    neptune_sim._nodes = {}
    neptune_sim._edges = []

    for p in profiles:
        neptune_sim._nodes[p["customer_id"]] = {
            "type": "account",
            "label": p["name"],
            "risk": p["risk_category"],
        }

    devices = {str(e["device_id"]) for e in raw_edges if e.get("device_id")}
    for dev_id in devices:
        neptune_sim._nodes[dev_id] = {"type": "device", "label": f"Device-{dev_id[:8]}"}

    ips = {str(e["ip_address"]) for e in raw_edges if e.get("ip_address")}
    for ip in ips:
        neptune_sim._nodes[ip] = {"type": "ip", "label": ip}

    transfers_map = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for edge in raw_edges:
        key = (edge["sender_account_no"], edge["receiver_account_no"])
        transfers_map[key]["amount"] += float(edge["amount"])
        transfers_map[key]["count"] += 1
        
    for (src, dst), stats in transfers_map.items():
        neptune_sim._edges.append((src, dst, "transfers_to", 
                                   {"total_amount": round(stats["amount"], 2), "count": stats["count"]}))

    device_edges = {(e["sender_account_no"], e["device_id"]) for e in raw_edges if e.get("device_id")}
    for acc, dev in device_edges:
        neptune_sim._edges.append((acc, str(dev), "uses_device", {"since": "2025-01"}))

    ip_edges = {(e["sender_account_no"], e["ip_address"]) for e in raw_edges if e.get("ip_address")}
    for acc, ip in ip_edges:
        neptune_sim._edges.append((acc, str(ip), "connects_from", {"frequency": "regular"}))

    print(f"  ✅ Simulators updated with {len(profiles)} profiles")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("\n" + "🔥" * 28)
    print("  BƯỚC 2: PUSH DỮ LIỆU TỪ JSON LÊN DATABASE")
    print("🔥" * 28)

    profiles, transactions, raw_edges = load_processed_data()

    # Always update simulators (fallback)
    update_simulators(profiles, transactions, raw_edges)

    # Push to cloud DBs
    results = {}
    results["Redis"] = push_redis(profiles, raw_edges)
    results["Neo4j"] = push_neo4j(profiles, raw_edges)
    results["MongoDB"] = push_mongodb(profiles, transactions)
    results["ChromaDB"] = push_chromadb()

    # Summary
    print("\n" + "=" * 55)
    print("  📋 KẾT QUẢ SEED")
    print("=" * 55)
    for name, ok in results.items():
        icon = "✅" if ok else "⏭️ "
        status = "Data đã push!" if ok else "Skipped (dùng simulator)"
        print(f"  {icon} {name}: {status}")

    ok_count = sum(1 for v in results.values() if v)
    print(f"\n  → {ok_count}/4 databases đã có data thật từ CSV")

    if ok_count < 4:
        print("\n  ⚠️  Các DB chưa push sẽ dùng SIMULATOR (data đã update từ CSV)")

    print(f"\n  ▶️  Tiếp theo: python main.py --serve\n")


if __name__ == "__main__":
    main()
