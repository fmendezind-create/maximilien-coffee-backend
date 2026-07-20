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
    allow_origins=["https://maximiliencoffee.com", "https://www.maximiliencoffee.com", "https://maximilien-coffee-web.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
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
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );

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

        CREATE TABLE IF NOT EXISTS skus (
            id SERIAL PRIMARY KEY,
            sku VARCHAR(50) UNIQUE NOT NULL,
            slug VARCHAR(100),
            weight VARCHAR(20),
            grind VARCHAR(50),
            description VARCHAR(200),
            created_at TIMESTAMP DEFAULT NOW()
        );

        INSERT INTO inventory (slug, name, sku_base, stock_250g, stock_454g, stock_500g)
        VALUES
            ('bourbon-rosado',    'Bourbon Rosado',    'MC-BR', 50, 30, 20),
            ('variedad-colombia', 'Variedad Colombia', 'MC-VC', 50, 30, 20),
            ('blend',             'Blend',             'MC-BL', 100, 50, 30)
        ON CONFLICT (slug) DO NOTHING;

        INSERT INTO skus (sku, slug, weight, grind, description) VALUES
            ('MC-BR-250G-GRN', 'bourbon-rosado', '250g', 'En grano', 'Bourbon Rosado 250g En Grano'),
            ('MC-BR-250G-FIL', 'bourbon-rosado', '250g', 'Filtro', 'Bourbon Rosado 250g Filtro'),
            ('MC-BR-250G-ESP', 'bourbon-rosado', '250g', 'Espresso', 'Bourbon Rosado 250g Espresso'),
            ('MC-BR-454G-GRN', 'bourbon-rosado', '454g', 'En grano', 'Bourbon Rosado 454g En Grano'),
            ('MC-BR-454G-FIL', 'bourbon-rosado', '454g', 'Filtro', 'Bourbon Rosado 454g Filtro'),
            ('MC-BR-500G-GRN', 'bourbon-rosado', '500g', 'En grano', 'Bourbon Rosado 500g En Grano'),
            ('MC-VC-250G-GRN', 'variedad-colombia', '250g', 'En grano', 'Variedad Colombia 250g En Grano'),
            ('MC-VC-250G-FIL', 'variedad-colombia', '250g', 'Filtro', 'Variedad Colombia 250g Filtro'),
            ('MC-VC-250G-ESP', 'variedad-colombia', '250g', 'Espresso', 'Variedad Colombia 250g Espresso'),
            ('MC-VC-454G-GRN', 'variedad-colombia', '454g', 'En grano', 'Variedad Colombia 454g En Grano'),
            ('MC-VC-500G-FIL', 'variedad-colombia', '500g', 'Filtro', 'Variedad Colombia 500g Filtro'),
            ('MC-BL-250G-GRN', 'blend', '250g', 'En grano', 'Blend 250g En Grano'),
            ('MC-BL-250G-ESP', 'blend', '250g', 'Espresso', 'Blend 250g Espresso'),
            ('MC-BL-454G-ESP', 'blend', '454g', 'Espresso', 'Blend 454g Espresso'),
            ('MC-BL-500G-ESP', 'blend', '500g', 'Espresso', 'Blend 500g Espresso')
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
                items, subtotal, discount, total, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
        """, (
            order.reference, "pending",
            order.customer_name, order.customer_email, order.customer_phone,
            order.customer_address, order.customer_city, order.customer_dept,
            json.dumps(items_with_sku),
            order.subtotal, order.discount, order.total, order.notes
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

@app.get("/inventory/{slug}")
def public_inventory(slug: str, db=Depends(get_db)):
    cur = db.cursor()
    cur.execute("SELECT slug, stock_250g, stock_454g, stock_500g FROM inventory WHERE slug = %s", (slug,))
    inv = cur.fetchone()
    if not inv:
        raise HTTPException(status_code=404, detail="Producto no encontrado")
    return dict(inv)
