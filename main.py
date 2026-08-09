from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
import os, hashlib, hmac, json
import psycopg2
from psycopg2.extras import RealDictCursor


# ── SKU GENERATOR ─────────────────────────────────────────────────
PRODUCT_CODES = {
    "bourbon-rosado":    "BR",
    "variedad-colombia": "VC",
    "blend":             "BL",
}
WEIGHT_CODES = {
    "250g": "250G",
    "454g": "454G",
    "500g": "500G",
}
GRIND_CODES = {
    "En grano":        "GRN",
    "Filtro":          "FIL",
    "Espresso":        "ESP",
    "Prensa francesa": "PRF",
    "Moka":            "MOK",
}

def generate_sku(slug: str, weight: str, grind: str) -> str:
    prod   = PRODUCT_CODES.get(slug, "XX")
    wt     = WEIGHT_CODES.get(weight, weight.upper().replace("G","G"))
    gr     = GRIND_CODES.get(grind, "GRN")
    return f"MC-{prod}-{wt}-{gr}"

app = FastAPI(title="Maximilien Coffee API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

# ── DATABASE ──────────────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor()

    # ── TABLA ORDERS (existente + extensiones) ────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            reference VARCHAR(100) UNIQUE NOT NULL,
            status VARCHAR(50) DEFAULT 'pending',
            customer_name VARCHAR(200),
            customer_email VARCHAR(200),
            customer_phone VARCHAR(50),
            customer_address TEXT,
            customer_city VARCHAR(100),
            customer_dept VARCHAR(100),
            items JSONB,
            subtotal INTEGER,
            discount INTEGER DEFAULT 0,
            total INTEGER,
            payment_method VARCHAR(50),
            wompi_transaction_id VARCHAR(200),
            tracking_number VARCHAR(100),
            carrier VARCHAR(50),
            notes TEXT,
            accepted_policy BOOLEAN DEFAULT FALSE,
            policy_accepted_at TIMESTAMP,
            shipped_at TIMESTAMP,
            delivered_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA CUSTOMERS (nueva) ───────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            email VARCHAR(200) UNIQUE NOT NULL,
            phone VARCHAR(50),
            address TEXT,
            city VARCHAR(100),
            dept VARCHAR(100),
            status VARCHAR(20) DEFAULT 'active',
            total_orders INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0,
            first_order_at TIMESTAMP,
            last_order_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA PLANS (nueva) ───────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id SERIAL PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            frequency VARCHAR(20) NOT NULL DEFAULT 'monthly',
            products JSONB,
            price INTEGER NOT NULL,
            original_price INTEGER,
            discount_pct INTEGER DEFAULT 0,
            shipping_cost INTEGER DEFAULT 0,
            status VARCHAR(20) DEFAULT 'active',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA SUBSCRIPTIONS (rediseñada) ─────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            reference VARCHAR(100) UNIQUE,
            customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
            plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL,
            customer_name VARCHAR(200),
            customer_email VARCHAR(200),
            customer_phone VARCHAR(50),
            customer_address TEXT,
            customer_city VARCHAR(100),
            customer_dept VARCHAR(100),
            plan_name VARCHAR(200),
            products JSONB,
            frequency VARCHAR(20) DEFAULT 'monthly',
            price INTEGER,
            status VARCHAR(30) DEFAULT 'pending',
            start_date TIMESTAMP,
            next_billing_date TIMESTAMP,
            last_billing_date TIMESTAMP,
            end_date TIMESTAMP,
            wompi_token VARCHAR(200),
            payment_method VARCHAR(50),
            cancellation_reason TEXT,
            cancelled_at TIMESTAMP,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA SUBSCRIPTION STATUS HISTORY (nueva) ─────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscription_status_history (
            id SERIAL PRIMARY KEY,
            subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE CASCADE,
            previous_status VARCHAR(30),
            new_status VARCHAR(30),
            reason TEXT,
            observation TEXT,
            changed_by VARCHAR(100) DEFAULT 'system',
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA SHIPMENTS (nueva) ───────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shipments (
            id SERIAL PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
            carrier VARCHAR(50),
            tracking_number VARCHAR(100),
            status VARCHAR(30) DEFAULT 'created',
            carrier_status VARCHAR(100),
            origin_city VARCHAR(100),
            destination_city VARCHAR(100),
            destination_dept VARCHAR(100),
            weight_kg DECIMAL(5,2),
            declared_value INTEGER,
            shipping_cost INTEGER,
            events JSONB DEFAULT '[]',
            shipped_at TIMESTAMP,
            delivered_at TIMESTAMP,
            estimated_delivery TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA AUDIT LOG (nueva) ───────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id SERIAL PRIMARY KEY,
            action VARCHAR(50) NOT NULL,
            entity VARCHAR(50) NOT NULL,
            entity_id VARCHAR(100),
            old_value JSONB,
            new_value JSONB,
            changed_by VARCHAR(100) DEFAULT 'admin',
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA INVENTORY (existente) ───────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id SERIAL PRIMARY KEY,
            slug VARCHAR(100) UNIQUE NOT NULL,
            name VARCHAR(200),
            sku_base VARCHAR(20),
            stock_250g INTEGER DEFAULT 0,
            stock_454g INTEGER DEFAULT 0,
            stock_500g INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── TABLA SKUS (existente) ────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS skus (
            id SERIAL PRIMARY KEY,
            sku VARCHAR(50) UNIQUE NOT NULL,
            slug VARCHAR(100),
            weight VARCHAR(20),
            grind VARCHAR(50),
            description VARCHAR(200),
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # ── ÍNDICES ───────────────────────────────────────────────────
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(customer_email);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at DESC);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_subscriptions_email ON subscriptions(customer_email);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);")

    # ── PLANES INICIALES ──────────────────────────────────────────
    cur.execute("""
        INSERT INTO plans (name, description, frequency, products, price, original_price, discount_pct, shipping_cost, status)
        VALUES
            ('Los Tres', 'Un café diferente cada mes — los tres en 250g', 'monthly',
             '[{"slug":"bourbon-rosado","weight":"250g","quantity":1},{"slug":"variedad-colombia","weight":"250g","quantity":1},{"slug":"blend","weight":"250g","quantity":1}]',
             84900, 95700, 11, 0, 'active'),
            ('El Doble', 'Tu favorito en grande — dos bolsas de 454g', 'monthly',
             '[{"slug":"bourbon-rosado","weight":"454g","quantity":2}]',
             97900, 119800, 18, 0, 'active')
        ON CONFLICT DO NOTHING;
    """)

    # ── INVENTARIO INICIAL ────────────────────────────────────────
    cur.execute("""
        INSERT INTO inventory (slug, name, sku_base, stock_250g, stock_454g, stock_500g)
        VALUES
            ('bourbon-rosado',    'Bourbon Rosado',    'MC-BR', 50, 30, 20),
            ('variedad-colombia', 'Variedad Colombia', 'MC-VC', 50, 30, 20),
            ('blend',             'Blend',             'MC-BL', 100, 50, 30)
        ON CONFLICT (slug) DO NOTHING;
    """)

    # ── SKUs ──────────────────────────────────────────────────────
    cur.execute("""
        INSERT INTO skus (sku, slug, weight, grind, description) VALUES
            ('MC-BR-250G-GRN', 'bourbon-rosado', '250g', 'En grano', 'Bourbon Rosado 250g En Grano'),
            ('MC-BR-250G-FIL', 'bourbon-rosado', '250g', 'Filtro', 'Bourbon Rosado 250g Filtro'),
            ('MC-BR-250G-ESP', 'bourbon-rosado', '250g', 'Espresso', 'Bourbon Rosado 250g Espresso'),
            ('MC-BR-454G-GRN', 'bourbon-rosado', '454g', 'En grano', 'Bourbon Rosado 454g En Grano'),
            ('MC-BR-454G-FIL', 'bourbon-rosado', '454g', 'Filtro', 'Bourbon Rosado 454g Filtro'),
            ('MC-VC-250G-GRN', 'variedad-colombia', '250g', 'En grano', 'Variedad Colombia 250g En Grano'),
            ('MC-VC-250G-FIL', 'variedad-colombia', '250g', 'Filtro', 'Variedad Colombia 250g Filtro'),
            ('MC-VC-250G-ESP', 'variedad-colombia', '250g', 'Espresso', 'Variedad Colombia 250g Espresso'),
            ('MC-VC-454G-GRN', 'variedad-colombia', '454g', 'En grano', 'Variedad Colombia 454g En Grano'),
            ('MC-BL-250G-GRN', 'blend', '250g', 'En grano', 'Blend 250g En Grano'),
            ('MC-BL-250G-ESP', 'blend', '250g', 'Espresso', 'Blend 250g Espresso'),
            ('MC-BL-454G-ESP', 'blend', '454g', 'Espresso', 'Blend 454g Espresso')
        ON CONFLICT (sku) DO NOTHING;
    """)

    conn.commit()
    cur.close()
    conn.close()

@app.on_event("startup")
def startup():
    init_db()

# ── MODELOS ───────────────────────────────────────────────────────
class OrderItem(BaseModel):
    slug: str
    name: str
    weight: str
    grind: str
    quantity: int
    unit_price: int

class CreateOrderRequest(BaseModel):
    reference: str
    customer_name: str
    customer_email: EmailStr
    customer_phone: str
    customer_address: Optional[str] = ""
    customer_city: Optional[str] = ""
    customer_dept: Optional[str] = ""
    items: List[OrderItem]
    subtotal: int
    discount: int = 0
    total: int
    notes: Optional[str] = ""

class WompiWebhookEvent(BaseModel):
    event: str
    data: dict
    sent_at: Optional[str] = None

# ── ENDPOINTS ─────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "ok", "service": "Maximilien Coffee API"}

@app.get("/health")
def health():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# Crear pedido (llamado desde el checkout antes de redirigir a Wompi)
@app.post("/orders")
def create_order(order: CreateOrderRequest, db=Depends(get_db)):
    cur = db.cursor()
    try:
        # Enriquecer items con SKU interno
        items_with_sku = []
        for item in order.items:
            item_dict = item.dict()
            item_dict["sku"] = generate_sku(item.slug, item.weight, item.grind)
            items_with_sku.append(item_dict)

        cur.execute("""
            INSERT INTO orders (
                reference, status, customer_name, customer_email, customer_phone,
                customer_address, customer_city, customer_dept,
                items, subtotal, discount, total, notes,
                accepted_policy, policy_accepted_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            order.reference, "pending",
            order.customer_name, order.customer_email, order.customer_phone,
            order.customer_address, order.customer_city, order.customer_dept,
            json.dumps(items_with_sku),
            order.subtotal, order.discount, order.total, order.notes,
            getattr(order, 'accepted_policy', False),
            getattr(order, 'policy_accepted_at', None)
        ))
        db.commit()
        result = dict(cur.fetchone())
        return {"ok": True, "order": result}
    except psycopg2.IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Referencia duplicada")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# Webhook de Wompi — actualiza estado del pedido
@app.post("/webhook/wompi")
async def wompi_webhook(request_body: dict, x_event_checksum: Optional[str] = Header(None), db=Depends(get_db)):
    event = request_body.get("event")
    if event != "transaction.updated":
        return {"ok": True}

    tx = request_body.get("data", {}).get("transaction", {})
    if not tx:
        return {"ok": True}

    reference = tx.get("reference", "")
    status = tx.get("status", "")
    wompi_id = tx.get("id", "")
    pay_method = tx.get("payment_method_type", "")

    # Mapear estado de Wompi a estado interno
    status_map = {
        "APPROVED": "paid",
        "DECLINED": "declined",
        "VOIDED": "voided",
        "ERROR": "error",
    }
    internal_status = status_map.get(status, "pending")

    cur = db.cursor()
    cur.execute("""
        UPDATE orders
        SET status = %s, wompi_transaction_id = %s, payment_method = %s, updated_at = NOW()
        WHERE reference = %s
        RETURNING *
    """, (internal_status, wompi_id, pay_method, reference))
    db.commit()

    return {"ok": True, "status": internal_status}

# ── PANEL ADMIN ───────────────────────────────────────────────────

def verify_admin(x_admin_key: Optional[str] = Header(None)):
    admin_key = os.environ.get("ADMIN_KEY", "")
    if not admin_key or x_admin_key != admin_key:
        raise HTTPException(status_code=401, detail="No autorizado")
    return True

@app.get("/admin/orders")
def list_orders(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db=Depends(get_db),
    _=Depends(verify_admin)
):
    cur = db.cursor()
    if status:
        cur.execute("SELECT * FROM orders WHERE status = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (status, limit, offset))
    else:
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT %s OFFSET %s", (limit, offset))
    orders = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT COUNT(*) as total FROM orders" + (" WHERE status = %s" if status else ""),
                (status,) if status else ())
    total = cur.fetchone()["total"]

    return {"orders": orders, "total": total, "limit": limit, "offset": offset}

@app.get("/admin/orders/{reference}")
def get_order(reference: str, db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("SELECT * FROM orders WHERE reference = %s", (reference,))
    order = cur.fetchone()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return dict(order)

@app.patch("/admin/orders/{reference}/status")
def update_order_status(reference: str, body: dict, db=Depends(get_db), _=Depends(verify_admin)):
    new_status = body.get("status")
    if new_status not in ["pending", "paid", "processing", "shipped", "delivered", "cancelled"]:
        raise HTTPException(status_code=400, detail="Estado inválido")
    cur = db.cursor()
    cur.execute("UPDATE orders SET status = %s, updated_at = NOW() WHERE reference = %s RETURNING *",
                (new_status, reference))
    db.commit()
    order = cur.fetchone()
    if not order:
        raise HTTPException(status_code=404, detail="Pedido no encontrado")
    return dict(order)

@app.get("/admin/stats")
def get_stats(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'paid') as paid_orders,
            COUNT(*) FILTER (WHERE status = 'pending') as pending_orders,
            COUNT(*) FILTER (WHERE status = 'shipped') as shipped_orders,
            COALESCE(SUM(total) FILTER (WHERE status = 'paid'), 0) as total_revenue,
            COUNT(*) as total_orders
        FROM orders
    """)
    stats = dict(cur.fetchone())

    cur.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as orders, COALESCE(SUM(total),0) as revenue
        FROM orders WHERE status = 'paid' AND created_at >= NOW() - INTERVAL '30 days'
        GROUP BY DATE(created_at) ORDER BY date
    """)
    daily = [dict(r) for r in cur.fetchall()]

    return {**stats, "daily_revenue": daily}

@app.get("/admin/inventory")
def get_inventory(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("SELECT * FROM inventory ORDER BY slug")
    return [dict(r) for r in cur.fetchall()]

@app.patch("/admin/inventory/{slug}")
def update_inventory(slug: str, body: dict, db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        UPDATE inventory
        SET stock_250g = %s, stock_454g = %s, stock_500g = %s, updated_at = NOW()
        WHERE slug = %s RETURNING *
    """, (body.get("stock_250g", 0), body.get("stock_454g", 0), body.get("stock_500g", 0), slug))
    db.commit()
    inv = cur.fetchone()
    if not inv:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return dict(inv)


@app.get("/admin/skus")
def list_skus(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("SELECT * FROM skus ORDER BY slug, weight, grind")
    return [dict(r) for r in cur.fetchall()]

@app.get("/skus/{slug}")
def get_skus_by_product(slug: str, db=Depends(get_db)):
    """Retorna todos los SKUs de un producto — útil para el frontend"""
    cur = db.cursor()
    cur.execute("SELECT sku, weight, grind, description FROM skus WHERE slug = %s ORDER BY weight", (slug,))
    return [dict(r) for r in cur.fetchall()]



# ── CUPONES ───────────────────────────────────────────────────────
# Los cupones viven en el servidor — nunca en el cliente
COUPONS: dict[str, int] = {
    "BIENVENIDO": 10,
    "ALMA10": 10,
    "HUILA15": 15,
}

class ValidateCouponRequest(BaseModel):
    code: str

@app.post("/coupons/validate")
def validate_coupon(req: ValidateCouponRequest):
    code = req.code.strip().upper()
    if code in COUPONS:
        return { "valid": True, "discount": COUPONS[code], "code": code }
    return { "valid": False, "discount": 0, "code": code }

# ── SUSCRIPCIONES ─────────────────────────────────────────────────

class CreateSubscriptionRequest(BaseModel):
    customer_name: str
    customer_email: EmailStr
    customer_phone: str
    customer_address: Optional[str] = ""
    customer_city: Optional[str] = ""
    product_slug: str
    product_name: str
    weight: str
    price_original: int
    price_discounted: int
    notes: Optional[str] = ""

@app.post("/subscriptions")
def create_subscription(sub: CreateSubscriptionRequest, db=Depends(get_db)):
    cur = db.cursor()
    try:
        # Crear tabla si no existe
        cur.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id SERIAL PRIMARY KEY,
                customer_name VARCHAR(200),
                customer_email VARCHAR(200),
                customer_phone VARCHAR(50),
                customer_address TEXT,
                customer_city VARCHAR(100),
                product_slug VARCHAR(100),
                product_name VARCHAR(200),
                weight VARCHAR(20),
                price_original INTEGER,
                price_discounted INTEGER,
                status VARCHAR(50) DEFAULT 'pending',
                notes TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            INSERT INTO subscriptions (
                customer_name, customer_email, customer_phone,
                customer_address, customer_city,
                product_slug, product_name, weight,
                price_original, price_discounted, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            sub.customer_name, sub.customer_email, sub.customer_phone,
            sub.customer_address, sub.customer_city,
            sub.product_slug, sub.product_name, sub.weight,
            sub.price_original, sub.price_discounted, sub.notes
        ))
        db.commit()
        result = cur.fetchone()
        return {"ok": True, "id": result["id"]}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/admin/subscriptions")
def list_subscriptions(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    try:
        cur.execute("SELECT * FROM subscriptions ORDER BY created_at DESC")
        return [dict(r) for r in cur.fetchall()]
    except:
        return []

@app.get("/inventory/{slug}")
def public_inventory(slug: str, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute("SELECT slug, stock_250g, stock_454g, stock_500g FROM inventory WHERE slug = %s", (slug,))
    inv = cur.fetchone()
    if not inv:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return dict(inv)

# ── CUSTOMERS ────────────────────────────────────────────────────

@app.get("/admin/customers")
def list_customers(
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    db=Depends(get_db),
    _=Depends(verify_admin)
):
    cur = db.cursor()
    where = []
    params = []
    if status:
        where.append("status = %s")
        params.append(status)
    if search:
        where.append("(name ILIKE %s OR email ILIKE %s OR phone ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    cur.execute(f"SELECT * FROM customers {where_sql} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [limit, offset])
    customers = [dict(r) for r in cur.fetchall()]
    cur.execute(f"SELECT COUNT(*) as total FROM customers {where_sql}", params)
    total = cur.fetchone()["total"]
    return {"customers": customers, "total": total}

@app.get("/admin/customers/{customer_id}")
def get_customer(customer_id: int, db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("SELECT * FROM customers WHERE id = %s", (customer_id,))
    customer = cur.fetchone()
    if not customer:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    cur.execute("SELECT * FROM orders WHERE customer_email = %s ORDER BY created_at DESC LIMIT 10",
                (customer["email"],))
    orders = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT * FROM subscriptions WHERE customer_email = %s ORDER BY created_at DESC",
                (customer["email"],))
    subs = [dict(r) for r in cur.fetchall()]
    return {**dict(customer), "recent_orders": orders, "subscriptions": subs}

# ── PLANS ────────────────────────────────────────────────────────

@app.get("/admin/plans")
def list_plans(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("SELECT * FROM plans ORDER BY created_at DESC")
    return [dict(r) for r in cur.fetchall()]

@app.get("/plans")
def public_plans(db=Depends(get_db)):
    cur = db.cursor()
    cur.execute("SELECT * FROM plans WHERE status = 'active' ORDER BY price")
    return [dict(r) for r in cur.fetchall()]

@app.patch("/admin/plans/{plan_id}/status")
def update_plan_status(plan_id: int, body: dict, db=Depends(get_db), _=Depends(verify_admin)):
    new_status = body.get("status")
    if new_status not in ["active", "inactive", "archived"]:
        raise HTTPException(status_code=400, detail="Estado inválido")
    cur = db.cursor()
    cur.execute("UPDATE plans SET status = %s, updated_at = NOW() WHERE id = %s RETURNING *",
                (new_status, plan_id))
    db.commit()
    plan = cur.fetchone()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan no encontrado")
    return dict(plan)

# ── SUBSCRIPTIONS (nuevos endpoints) ─────────────────────────────

@app.get("/admin/subscriptions/stats")
def subscription_stats(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'active') as active_count,
            COUNT(*) FILTER (WHERE status = 'cancelled') as cancelled_count,
            COUNT(*) FILTER (WHERE status = 'pending') as pending_count,
            COUNT(*) as total_count,
            COALESCE(SUM(price) FILTER (WHERE status = 'active'), 0) as mrr,
            COALESCE(AVG(price) FILTER (WHERE status = 'active'), 0) as arpu
        FROM subscriptions
    """)
    stats = dict(cur.fetchone())
    stats["arr"] = stats["mrr"] * 12
    active = stats["active_count"] or 1
    cancelled = stats["cancelled_count"] or 0
    stats["churn_rate"] = round(cancelled / (active + cancelled) * 100, 1)
    stats["retention_rate"] = round(100 - stats["churn_rate"], 1)

    cur.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as new_subs
        FROM subscriptions
        WHERE created_at >= NOW() - INTERVAL '30 days'
        GROUP BY DATE(created_at) ORDER BY date
    """)
    stats["daily_new"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT plan_name, COUNT(*) as count, COALESCE(SUM(price),0) as revenue
        FROM subscriptions WHERE status = 'active'
        GROUP BY plan_name ORDER BY count DESC
    """)
    stats["by_plan"] = [dict(r) for r in cur.fetchall()]

    return stats

@app.patch("/admin/subscriptions/{sub_id}/status")
def update_subscription_status(sub_id: int, body: dict, db=Depends(get_db), _=Depends(verify_admin)):
    valid_statuses = ["pending", "active", "paused", "cancelled", "payment_failed", "expired", "suspended"]
    new_status = body.get("status")
    if new_status not in valid_statuses:
        raise HTTPException(status_code=400, detail="Estado inválido")
    cur = db.cursor()
    cur.execute("SELECT status FROM subscriptions WHERE id = %s", (sub_id,))
    sub = cur.fetchone()
    if not sub:
        raise HTTPException(status_code=404, detail="Suscripción no encontrada")
    old_status = sub["status"]
    cur.execute("""
        UPDATE subscriptions SET status = %s, updated_at = NOW()
        WHERE id = %s RETURNING *
    """, (new_status, sub_id))
    db.commit()
    result = dict(cur.fetchone())
    cur.execute("""
        INSERT INTO subscription_status_history
        (subscription_id, previous_status, new_status, reason, changed_by)
        VALUES (%s, %s, %s, %s, 'admin')
    """, (sub_id, old_status, new_status, body.get("reason", "")))
    db.commit()
    cur.execute("""
        INSERT INTO audit_log (action, entity, entity_id, old_value, new_value)
        VALUES ('status_change', 'subscription', %s, %s, %s)
    """, (str(sub_id), json.dumps({"status": old_status}), json.dumps({"status": new_status})))
    db.commit()
    return result

# ── SHIPMENTS ─────────────────────────────────────────────────────

@app.get("/admin/shipments")
def list_shipments(limit: int = 50, offset: int = 0, db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        SELECT s.*, o.reference as order_reference, o.customer_name, o.customer_city
        FROM shipments s
        LEFT JOIN orders o ON s.order_id = o.id
        ORDER BY s.created_at DESC LIMIT %s OFFSET %s
    """, (limit, offset))
    return [dict(r) for r in cur.fetchall()]

@app.post("/admin/shipments")
def create_shipment(body: dict, db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        INSERT INTO shipments (order_id, carrier, tracking_number, status,
            destination_city, destination_dept, weight_kg, declared_value, shipping_cost)
        VALUES (%s, %s, %s, 'created', %s, %s, %s, %s, %s)
        RETURNING *
    """, (
        body.get("order_id"), body.get("carrier"), body.get("tracking_number"),
        body.get("destination_city"), body.get("destination_dept"),
        body.get("weight_kg", 0.3), body.get("declared_value", 0),
        body.get("shipping_cost", 0)
    ))
    db.commit()
    shipment = dict(cur.fetchone())
    if body.get("order_id"):
        cur.execute("""
            UPDATE orders SET tracking_number = %s, carrier = %s,
            status = 'shipped', shipped_at = NOW(), updated_at = NOW()
            WHERE id = %s
        """, (body.get("tracking_number"), body.get("carrier"), body.get("order_id")))
        db.commit()
    return shipment

# ── ANALYTICS (Power BI ready) ────────────────────────────────────

@app.get("/api/analytics/sales")
def analytics_sales(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    db=Depends(get_db),
    _=Depends(verify_admin)
):
    cur = db.cursor()
    where = "WHERE status = 'paid'"
    params = []
    if from_date:
        where += " AND created_at >= %s"
        params.append(from_date)
    if to_date:
        where += " AND created_at <= %s"
        params.append(to_date)
    cur.execute(f"""
        SELECT
            DATE(created_at) as date,
            COUNT(*) as orders,
            SUM(total) as revenue,
            AVG(total) as avg_ticket,
            SUM(discount) as total_discount
        FROM orders {where}
        GROUP BY DATE(created_at)
        ORDER BY date
    """, params)
    return {"data": [dict(r) for r in cur.fetchall()]}

@app.get("/api/analytics/subscriptions")
def analytics_subscriptions(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        SELECT
            DATE(created_at) as date,
            COUNT(*) as new_subscriptions,
            COUNT(*) FILTER (WHERE status = 'cancelled') as cancellations,
            SUM(price) FILTER (WHERE status = 'active') as mrr_contribution
        FROM subscriptions
        GROUP BY DATE(created_at)
        ORDER BY date
    """)
    return {"data": [dict(r) for r in cur.fetchall()]}

@app.get("/api/analytics/customers")
def analytics_customers(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        SELECT
            customer_email,
            customer_name,
            customer_city,
            customer_dept,
            COUNT(*) as total_orders,
            SUM(total) as total_spent,
            MIN(created_at) as first_order,
            MAX(created_at) as last_order
        FROM orders WHERE status = 'paid'
        GROUP BY customer_email, customer_name, customer_city, customer_dept
        ORDER BY total_spent DESC
    """)
    return {"data": [dict(r) for r in cur.fetchall()]}

@app.get("/api/analytics/products")
def analytics_products(db=Depends(get_db), _=Depends(verify_admin)):
    cur = db.cursor()
    cur.execute("""
        SELECT
            item->>'slug' as slug,
            item->>'name' as name,
            item->>'weight' as weight,
            SUM((item->>'quantity')::int) as units_sold,
            SUM((item->>'unit_price')::int * (item->>'quantity')::int) as revenue
        FROM orders, jsonb_array_elements(items) as item
        WHERE status = 'paid'
        GROUP BY slug, name, weight
        ORDER BY revenue DESC
    """)
    return {"data": [dict(r) for r in cur.fetchall()]}

