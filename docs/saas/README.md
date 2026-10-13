# SaaS de trading automatizado — plan del proyecto

> **Estado:** planificación (octubre 2026). Nada de esto está construido todavía.
> La base técnica es el bot que ya funciona en este repo (`xrp_futures/`).

## Qué es

Un servicio web por **suscripción fija** donde cada usuario conecta **su propia cuenta de
Binance** (con su propia API key, sin permiso de retiro) y **configura su propio bot** de
gestión automática: seguimiento de tendencia, stops automáticos, límite de riesgo por
operación y kill-switch. Nosotros vendemos la **herramienta**; las decisiones, las ganancias
y los riesgos son **100% del usuario**. Nunca tocamos su dinero.

**Para quién:** personas en Chile que quieren entrar al mercado cripto con reglas claras y
riesgo controlado, en vez de operar a mano, apalancados y sin plan.

## Documentos

| Documento | Contenido |
|---|---|
| [01 — Plan y fases](01_plan_y_fases.md) | Visión, modelo de negocio, planes, fases de construcción, métricas y riesgos |
| [02 — Arquitectura](02_arquitectura.md) | Componentes (Vercel + Supabase + motor en Oracle), flujos, escalado, costos |
| [03 — Base de datos](03_base_de_datos.md) | Tablas de Supabase, seguridad por fila (RLS), retención de datos |
| [04 — Legal](04_legal.md) | Ley Fintec y CMF: qué evitar, cómo diseñar el producto, pasos legales |
| [05 — Seguridad](05_seguridad.md) | Manejo de API keys, autenticación, auditoría, incidentes |
| [06 — Producto y front](06_producto_y_front.md) | Pantallas, onboarding, configuración, tono y textos permitidos |
| [07 — Investigación de mercado](07_investigacion_mercado.md) | Competencia, tamaño del mercado (Chile / LatAm / mundo), lecciones de la industria, historial necesario para generar confianza, viabilidad |

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Cobro | **Suscripción fija.** Sin % de ganancias ni comisión por operación. |
| Cuenta del usuario | Su propia cuenta de Binance + su propia API key (**sin retiros**, IP restringida a la nuestra). |
| Custodia | **Ninguna.** Jamás recibimos ni movemos dinero de usuarios. |
| Base de datos y login | **Supabase** (PostgreSQL + Supabase Auth), región São Paulo. |
| Front | **Next.js en Vercel.** |
| Motor de bots | **VM en Oracle Cloud (Santiago)**, con IP fija. No puede ir en Vercel (ver arquitectura). |
| Arquitectura del motor | **Un sistema multi-usuario**, no una VM por usuario. |
| Comunicación | Tono de herramienta/SaaS. **Nunca** publicitar rentabilidades ("gana X%"). |

## Decisiones pendientes

| Tema | Propuesta | Por confirmar |
|---|---|---|
| Cómo configura el usuario su bot | Plantillas neutrales (conservadora / estándar) + ajustes propios | ✔︎ con el usuario y el abogado |
| Precios de los planes | ~5.000–10.000 CLP/mes | Validar con lista de espera |
| Pasarela de pago | Flow o Mercado Pago (suscripciones) | Comparar comisiones |
| Inscripción en la CMF | Consulta formal a la CMF antes de lanzar | Respuesta de la CMF / abogado |
| Primer paso | Fase 0: validar demanda + consulta legal | ✔︎ |

## Relación con el bot actual

El motor del SaaS reutiliza los módulos probados de `xrp_futures/execution/` (señal,
sizing, stops en Binance, reconciliación, kill-switch, recuperación de órdenes perdidas,
candado de proceso). El cambio principal es pasar de **un usuario con archivos locales**
(`live_state/`) a **muchos usuarios con su estado en Supabase**. Ver
[02 — Arquitectura](02_arquitectura.md#de-un-usuario-a-muchos).
