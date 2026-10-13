# 06 — Producto y front

## Principios

- **Aspecto de herramienta, no de casino ni de inversión:** sobrio, claro, sin cohetes,
  sin "gana X%", sin cuentas regresivas.
- **El usuario decide:** cada opción explica qué hace y qué riesgo implica.
- **Riesgo siempre visible:** capital, caída desde el máximo y kill-switch a la vista.
- Reutilizar el diseño editorial del dashboard actual (sin emojis, color solo para
  ganancias/pérdidas/avisos).

## Pantallas

| Pantalla | Contenido |
|---|---|
| **Landing** | Qué es, cómo funciona, riesgos, precios, preguntas frecuentes, lista de espera |
| **Registro / login** | Email + contraseña, verificación, 2FA, opcional Google |
| **Onboarding** | 1) Aceptar términos y aviso de riesgo · 2) Guía para crear la API key en Binance (sin retiros, IP restringida) · 3) Pegar la key (se cifra en el navegador) · 4) Verificación automática de permisos · 5) Elegir plantilla y configurar · 6) Empezar en testnet |
| **Inicio** | Capital, resultado del bot, estado (activo/en pausa), botón activar/desactivar |
| **Consola** | Lo que hizo el bot, en lenguaje simple (eventos) |
| **Monedas** | Detalle por moneda: posición, stop, historial |
| **Capital** | Gráfico con zoom, rangos (24 h / 7 días / 30 días / todo), caída desde el máximo |
| **Configuración** | Plantilla, monedas, riesgo por operación, capital asignado, apalancamiento, kill-switch |
| **Cuenta** | Datos personales, seguridad (2FA, sesiones), API key (estado, revocar) |
| **Suscripción** | Plan, próximo cobro, historial de pagos, cambiar o cancelar |
| **Legal** | Términos, privacidad, aviso de riesgo |

## Configuración del bot (propuesta)

| Opción | Rango | Por defecto |
|---|---|---|
| Plantilla | Conservadora / Estándar / Personalizada | Conservadora |
| Monedas | De la lista soportada (BTC, ETH, XRP, BNB, SOL, ADA, LTC, LINK) | Según plantilla |
| Riesgo por operación | 0,1% – 2% del capital | 0,3% (conservadora) / 0,6% (estándar) |
| Capital asignado | Monto en USDT o "todo el saldo" | Todo el saldo |
| Apalancamiento | 1x – 3x (margen aislado) | 3x |
| Kill-switch | Caída máxima 10% – 40% | 30% |
| Modo | Testnet / Real | **Testnet** |

Cada opción muestra, en lenguaje simple, **qué cambia** (ej.: "más riesgo = operaciones más
grandes: ganancias y pérdidas más grandes") y, cuando aplica, **qué pasó históricamente**
como referencia educativa, con su advertencia ("resultados pasados no garantizan
resultados futuros").

El bot **arranca en pausa** y en **testnet**; pasar a dinero real requiere 2FA y una
confirmación explícita.

## Tono y textos

| ✅ Sí | ❌ No |
|---|---|
| "Tus reglas, tu cuenta." | "Gana dinero mientras duermes." |
| "Gestión automática de riesgo con stops en Binance." | "Rentabilidad del X% anual." |
| "Puedes perder dinero. Configura un riesgo que puedas asumir." | "Inversión segura." |
| "El bot sigue tendencias: varias pérdidas chicas y algunas ganancias grandes." | "Nuestro algoritmo nunca pierde." |
| "Tu dinero nunca sale de tu cuenta de Binance." | "Deposita con nosotros." |

## Stack del front

- **Next.js** (App Router) en **Vercel**.
- **Supabase JS** para login y datos (con RLS) y **Realtime** para que el panel se actualice
  solo.
- Gráficos con zoom (los del dashboard actual son la referencia funcional).
- Pagos: Flow o Mercado Pago (suscripciones + webhooks en rutas API de Next.js).
