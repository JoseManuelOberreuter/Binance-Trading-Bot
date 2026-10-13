# 01 — Plan y fases

## Propuesta de valor

- **Riesgo controlado por diseño:** cada operación arriesga un % pequeño y configurable del
  capital, con stop automático puesto en Binance (protege aunque nuestro sistema se caiga).
- **Reglas en vez de emociones:** el bot sigue reglas fijas; el usuario no tiene que mirar
  gráficos ni decidir a cada rato.
- **Tu cuenta, tus reglas:** el dinero nunca sale de la cuenta Binance del usuario; puede
  pausar, cambiar el riesgo o desconectar en cualquier momento.
- **Transparencia:** panel con capital, operaciones, comisiones, funding y caídas, en
  lenguaje simple.

**No es:** un fondo, un asesor, una promesa de rentabilidad ni un servicio de custodia.

## Modelo de negocio

- **Suscripción fija mensual o anual.** El precio no depende de si el usuario gana o pierde.
- **Sin** % de ganancias y **sin** comisión por operación (ver [04 — Legal](04_legal.md)).
- **Ingreso adicional posible:** programa de Binance para plataformas (Binance Link /
  Broker), que devuelve parte de las comisiones que Binance cobra a los usuarios. Lo paga
  Binance, no el usuario. *Verificar condiciones vigentes.*

### Planes (propuesta, a validar)

| Plan | Precio (CLP/mes) | Incluye |
|---|---|---|
| Prueba | Gratis 14 días | Solo **testnet** (dinero de prueba) |
| Básico | ~5.000 | 1 bot, hasta 4 monedas, ciclo de 5 min |
| Pro | ~10.000 | 1 bot, 8 monedas, ciclo de 1 min, alertas |

Los límites por plan también controlan el uso de la API de Binance (ver escalado en
[02 — Arquitectura](02_arquitectura.md#escalado-y-limites-de-binance)).

### Números de referencia (para decidir precios)

- Comisiones de Binance Futures para el usuario: ~1–2% de su capital al año con esta
  estrategia (backtest), más funding.
- Una suscripción solo tiene sentido si es pequeña frente al capital del usuario:
  12 × 5.000 CLP ≈ 60.000 CLP/año ≈ 6% de 1.000.000 CLP. Con capitales chicos el plan se
  come la ganancia → **comunicar un capital mínimo recomendado**.

## Fases

Cada fase tiene un criterio de salida. No se avanza sin cumplirlo.

| Fase | Qué se hace | Criterio para avanzar |
|---|---|---|
| **0. Validar** | Landing con lista de espera · consulta formal a la CMF / abogado fintech · el bot real del fundador operando con su propio dinero | ≥ 50 inscritos interesados · respuesta legal favorable o camino claro · ≥ 3 meses de historial real |
| **1. Motor multi-usuario** | Adaptar el motor: estado en Supabase, N usuarios, datos de mercado compartidos, keys cifradas, workers aislados | Tests verdes · 10 usuarios simulados en testnet sin interferirse |
| **2. API y datos** | Esquema Supabase + RLS · funciones para conectar key, configurar, comandos | Un usuario puede conectar su key testnet y ver su estado |
| **3. Front** | Next.js en Vercel: login, onboarding, configuración, panel, consola | Flujo completo de punta a punta en testnet |
| **4. Pagos** | Suscripciones con Flow / Mercado Pago · webhooks · acceso según plan | Alta, cobro, renovación y baja funcionando |
| **5. Seguridad** | Revisión de seguridad completa (ver [05](05_seguridad.md)) · respaldos · alertas | Checklist de seguridad cerrado |
| **6. Beta cerrada** | 5–10 personas conocidas, gratis, primero en testnet y luego real con montos chicos | 30 días sin incidentes graves |
| **7. Lanzamiento** | Apertura al público · soporte · términos legales publicados | — |

### Fase 0 en detalle (lo primero)

1. **Landing simple** (Vercel): qué es, cómo funciona, riesgos, formulario de lista de espera
   (email + capital aproximado + experiencia). Sin promesas de rentabilidad.
2. **Consulta formal a la CMF** describiendo el modelo (herramienta configurable, cuenta del
   usuario, suscripción fija). Ver [04 — Legal](04_legal.md).
3. **Historial real:** el bot de este repo operando con dinero del fundador. Es la mejor
   prueba de que el sistema funciona (no se publicita como rentabilidad; sirve para
   detectar fallas).
4. **Constitución de una SpA** cuando haya señales de demanda (para facturar y separar
   responsabilidades).

## Métricas a seguir

- Producto: usuarios activos, bots activos, % que completa el onboarding, bajas por mes.
- Técnicas: ciclos fallidos, órdenes sin confirmar, posiciones sin stop (debe ser **0**),
  uso de la API de Binance por IP.
- Negocio: ingresos mensuales recurrentes, costo por usuario, tiempo de soporte.

## Riesgos del proyecto

| Riesgo | Mitigación |
|---|---|
| Interpretación legal (asesoría) | Diseño de herramienta configurable + consulta CMF + abogado |
| Un fallo del motor afecta dinero real | Stops siempre en Binance · recuperación de órdenes · kill-switch global · beta en testnet |
| Filtración de API keys | Cifrado asimétrico, keys sin retiro y con IP restringida (ver [05](05_seguridad.md)) |
| Límites de la API de Binance | Datos de mercado compartidos · límites por plan · varias IPs al crecer |
| Usuarios que pierden dinero y culpan al servicio | Términos claros · avisos de riesgo · sin promesas · capital mínimo recomendado |
| Cambios de reglas de Binance | Monitoreo de la API · reconciliación con Binance como fuente de verdad |
| Baja demanda | Validar en la fase 0 antes de construir |
