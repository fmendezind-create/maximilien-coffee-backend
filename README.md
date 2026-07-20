# Maximilien Coffee — Backend FastAPI

## Deploy en Railway

1. Crea cuenta en railway.app
2. New Project → Deploy from GitHub repo
3. Add PostgreSQL database
4. Configura variables de entorno:
   - DATABASE_URL (Railway la agrega automáticamente)
   - ADMIN_KEY=tu-llave-secreta
5. Deploy

## Endpoints principales

### Público
- GET  /health — estado del servidor
- GET  /inventory/{slug} — stock disponible

### Pedidos (llamado desde Next.js)
- POST /orders — crear pedido
- POST /webhook/wompi — webhook de Wompi

### Admin (requiere X-Admin-Key header)
- GET  /admin/orders — listar pedidos
- GET  /admin/orders/{ref} — detalle del pedido
- PATCH /admin/orders/{ref}/status — cambiar estado
- GET  /admin/stats — estadísticas
- GET  /admin/inventory — inventario
- PATCH /admin/inventory/{slug} — actualizar stock
