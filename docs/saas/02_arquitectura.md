# 02 — Arquitectura

## Vista general

```
                    ┌────────────────────────────┐
  Usuario ────────► │  App web (Next.js)          │  Vercel
  (navegador)       │  login · onboarding · panel │
                    └──────────────┬─────────────┘
                                   │ (Supabase JS, sesión del usuario, RLS)
                    ┌──────────────▼─────────────┐
                    │  Supabase (São Paulo)       │
                    │  PostgreSQL · Auth · Realtime│
                    └──────────────▲─────────────┘
                                   │ (service role, solo desde el motor)
                    ┌──────────────┴─────────────┐
                    │  Motor de bots (Python)     │  VM Oracle Cloud (Santiago)
                    │  scheduler · workers        │  IP pública fija
                    └──────────────┬─────────────┘
                                   │ (API key de cada usuario)
                    ┌──────────────▼─────────────┐
                    │  Binance USDⓈ-M Futures     │
                    └────────────────────────────┘
```

El front y el motor **no se hablan directamente**: ambos leen y escriben en Supabase.
Así el motor puede seguir operando aunque el front esté caído, y viceversa.

## Componentes

### 1. App web — Next.js en Vercel
- Login y registro (Supabase Auth), onboarding, configuración, panel, consola, suscripción.
- Usa la sesión del usuario: **solo ve sus propios datos** (RLS en Supabase).
- Cifra la API key **en el navegador** antes de enviarla (ver [05 — Seguridad](05_seguridad.md)).
- Recibe los webhooks de la pasarela de pago (rutas API de Next.js).
- **Nota de costo:** el plan gratuito de Vercel (Hobby) es para uso no comercial; un SaaS que
  cobra necesita el plan Pro.

### 2. Supabase
- **PostgreSQL:** todas las tablas (ver [03 — Base de datos](03_base_de_datos.md)).
- **Auth:** email + contraseña, verificación de email, 2FA, opcional "iniciar con Google".
- **Realtime:** el panel se actualiza solo cuando el motor escribe eventos o capital.
- Región **São Paulo** (la más cercana a Chile).
- **Nota de costo:** el plan gratuito pausa proyectos inactivos y tiene límites de tamaño;
  en producción usar el plan Pro.

### 3. Motor de bots — Python en VM Oracle (Santiago)
Por qué **no** puede ir en Vercel:
1. Binance bloquea IPs de EE.UU. (error 451), y Vercel ejecuta mayormente desde allá.
2. Las funciones de Vercel duran segundos; el bot debe correr 24/7.
3. No tiene IP fija, y cada usuario restringe su key a **nuestra** IP.

Partes del motor:
- **Scheduler:** cada ciclo (1 o 5 min según plan) decide qué bots procesar.
- **Datos de mercado compartidos:** las velas de 12h y las señales de cada moneda se
  calculan **una sola vez** para todos los usuarios (la señal es la misma; lo que cambia es
  el tamaño de cada posición según el capital y riesgo del usuario).
- **Workers:** procesan los bots de los usuarios en paralelo. Cada bot es independiente:
  su estado, su configuración, su key. El error de un usuario nunca afecta a otro.
- **Candado por usuario:** un bot de un usuario nunca corre dos veces a la vez (hoy es
  `process_lock.py`; en multi-usuario será un lock en la base de datos).

## De un usuario a muchos

| Hoy (`xrp_futures/`) | En el SaaS |
|---|---|
| `live_state/<env>/state.json` (`StateStore`) | Tablas `bot_state`, `positions` |
| `trades.jsonl`, `equity_log.jsonl`, `events.jsonl` | Tablas `trades`, `equity_snapshots`, `events` |
| Buzón de comandos en archivos (`CommandQueue`) | Tabla `commands` |
| `LiveConfig` + `SET_CAPITAL` / `SET_RISK` | Tabla `bot_configs` (editada desde el front) |
| Una API key en `.env` | `exchange_keys` cifradas, una por usuario |
| `ProcessLock` (archivo) | Lock por usuario en la base de datos |
| Streamlit | Next.js |

**Se reutiliza sin cambios** la lógica probada: `live_signal`, `sizing`, `symbol_filters`,
`reconcile`, `order_manager` (stops algo, precio mark, recuperación de órdenes con respuesta
perdida, verificación de margen aislado), `kill_switch`.

## Flujos principales

**Registro y conexión de la cuenta**
1. Usuario se registra (Supabase Auth) y verifica su email.
2. Onboarding: crea su API key en Binance siguiendo la guía (solo lectura + Futures, **sin
   retiros**, IP restringida a la IP del motor).
3. El front cifra la key y la guarda en `exchange_keys`.
4. El motor la descifra, **verifica los permisos** con Binance (rechaza si permite retiros) y
   marca la key como válida.

**Activar el bot**
1. Usuario elige plantilla y ajustes → `bot_configs`.
2. Presiona "Activar" → `commands` (`RESUME_BOT`).
3. En el siguiente ciclo el motor aplica el comando y empieza a operar.

**Ciclo del motor (por usuario)**
Igual al bot actual: sincroniza reloj → resuelve órdenes pendientes → reconcilia con
Binance → aplica comandos → si cerró vela de 12h: salidas, stops, entradas → guarda estado,
capital y eventos en Supabase.

**Pago**
Pasarela → webhook (Vercel) → actualiza `subscriptions`. Si la suscripción vence, el motor
**pausa** el bot (no abre posiciones nuevas) pero **mantiene los stops** de lo que esté
abierto, y avisa al usuario.

## Escalado y límites de Binance

- Binance limita a **2.400 unidades de peso por minuto por IP**. Todas las llamadas de
  todos los usuarios salen por la misma IP del motor.
- Un ciclo de un usuario usa ~15 unidades. Con ciclos de 1 minuto caben ~100–150 usuarios
  por IP (con margen de seguridad).
- Cómo crecer: ciclos de 5 min en planes básicos (5 veces más usuarios), datos de mercado
  compartidos, y al superar el límite, **más VMs con su propia IP** (cada usuario queda
  asignado a una IP, que es la que pone en su key).

## Entornos

- **Desarrollo:** local + Binance testnet + proyecto Supabase de desarrollo.
- **Producción:** VM Oracle + proyecto Supabase de producción + Vercel.
- Cada usuario puede tener bot en **testnet** (prueba) o **real**; nunca se mezclan.

## Costos estimados (MVP)

| Servicio | Costo |
|---|---|
| Oracle VM (Always Free, hasta 4 CPU / 24 GB) | $0 (requiere conseguir capacidad) |
| Supabase | $0 en desarrollo · ~USD 25/mes en producción (plan Pro) |
| Vercel | $0 en desarrollo · ~USD 20/mes en producción (plan Pro, uso comercial) |
| Dominio | ~USD 10–15/año |
| Pasarela de pago | % por transacción (según Flow / Mercado Pago) |
| Abogado / CMF | Variable (consulta y términos) |

*Precios aproximados; verificar los vigentes antes de decidir.*
