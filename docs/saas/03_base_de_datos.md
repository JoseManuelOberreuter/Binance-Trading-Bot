# 03 — Base de datos (Supabase / PostgreSQL)

Todas las tablas con datos de usuario tienen `user_id` y **RLS (Row Level Security)**
activado: un usuario solo puede leer/escribir sus propias filas. El motor usa la clave
`service_role` (solo en la VM, nunca en el front) y por eso puede ver todos los bots.

## Tablas

### Cuentas y suscripción

**`profiles`** — datos del usuario (complementa `auth.users` de Supabase)
| Columna | Tipo | Nota |
|---|---|---|
| `user_id` | uuid PK → auth.users | |
| `full_name` | text | |
| `country` | text | Para saber qué ley aplica |
| `accepted_terms_at` | timestamptz | Versión de términos aceptada |
| `terms_version` | text | |
| `risk_ack_at` | timestamptz | Aceptó el aviso de riesgo |
| `created_at` | timestamptz | |

**`subscriptions`**
| Columna | Tipo | Nota |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid | |
| `plan` | text | `trial` / `basic` / `pro` |
| `status` | text | `active` / `past_due` / `canceled` |
| `current_period_end` | timestamptz | Hasta cuándo tiene acceso |
| `provider` / `provider_ref` | text | Flow / Mercado Pago y su id |

**`payments`** — historial de cobros (monto, fecha, estado, id del proveedor).

### Conexión con Binance

**`exchange_keys`**
| Columna | Tipo | Nota |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid | |
| `environment` | text | `testnet` / `production` |
| `api_key_ciphertext` | bytea | **Cifrada** (ver [05](05_seguridad.md)) |
| `api_secret_ciphertext` | bytea | **Cifrada** |
| `key_hint` | text | Últimos 4 caracteres, para mostrar |
| `status` | text | `pending` / `valid` / `rejected` / `revoked` |
| `can_withdraw` | boolean | Debe ser **false**; si es true, se rechaza |
| `ip_restricted` | boolean | |
| `checked_at` | timestamptz | Última verificación con Binance |

> El front puede **insertar** una key cifrada pero **nunca leer** el texto cifrado de vuelta
> (la política RLS solo expone `key_hint`, `status` y fechas).

### Bot

**`bot_configs`** — lo que el usuario decide (hoy: `LiveConfig`, `SET_CAPITAL`, `SET_RISK`)
| Columna | Tipo | Nota |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | uuid | Un bot por usuario en el MVP |
| `environment` | text | `testnet` / `production` |
| `template` | text | `conservadora` / `estandar` / `personalizada` |
| `symbols` | text[] | Monedas elegidas |
| `risk_pct` | numeric | 0,1%–2% |
| `assigned_capital` | numeric | null = todo el saldo de Futures |
| `leverage` | int | Máximo permitido por plan |
| `kill_switch_dd` | numeric | Ej. 0,30 |
| `paused` | boolean | Arranca **true** |
| `updated_at` | timestamptz | |

**`bot_state`** — lo que el motor recuerda (hoy: `state.json`)
| Columna | Tipo | Nota |
|---|---|---|
| `bot_id` | uuid PK → bot_configs | |
| `simulated_capital` | numeric | Base del capital asignado |
| `realized_pnl` | numeric | |
| `kill_switch` | jsonb | pico, activado, etc. |
| `last_processed_bar` | timestamptz | |
| `last_cycle_at` | timestamptz | Latido para el panel |
| `pending_entries` | jsonb | Órdenes con respuesta perdida |
| `locked_by` / `locked_until` | text / timestamptz | Lock por usuario |

**`positions`** — posiciones abiertas por bot (símbolo, lado, cantidad, entrada, stop,
id del stop en Binance, comisión de entrada).

**`trades`** — operaciones cerradas (entrada, salida, motivo, PnL, comisiones, funding).

**`equity_snapshots`** — curva de capital cada 5 min (capital, realizado, no realizado,
caída desde el máximo, posiciones).

**`events`** — la consola en lenguaje simple (tipo, datos en jsonb, fecha).

**`commands`** — buzón del front al motor (`PAUSE_BOT`, `RESUME_BOT`, `CLOSE_POSITION`,
`SET_RISK`, `SET_CAPITAL`, …) con estado `pending` / `applied` / `failed` y mensaje.

### Mercado (compartido, sin `user_id`)

**`market_signals`** — señal por moneda y vela de 12h (señal, ATR, niveles Chandelier).
La calcula el motor una vez y la usan todos los bots.

### Auditoría

**`audit_log`** — quién hizo qué y cuándo: inicio de sesión, cambio de key, cambio de
configuración, comandos. Solo inserción; nadie puede editar ni borrar.

## Índices

- `(user_id)` en todas las tablas de usuario.
- `(bot_id, created_at desc)` en `events`, `equity_snapshots`, `trades`.
- `(status, created_at)` en `commands` (el motor busca pendientes).

## Retención

- `equity_snapshots`: detalle de 5 min por 90 días; después, un punto por día.
- `events`: 180 días.
- `trades`, `payments`, `audit_log`: se conservan (respaldo contable y de seguridad).
- Al borrar una cuenta: se eliminan keys y datos personales; los registros contables se
  anonimizan según la ley.
