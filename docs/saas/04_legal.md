# 04 — Legal (Chile)

> Orientación basada en la normativa pública (Ley Fintec 21.521, NCG 502 de la CMF y sus
> preguntas frecuentes), revisada en octubre 2026. **No es asesoría legal.** Antes de lanzar,
> validar con un abogado fintech y con una consulta formal a la CMF.

## Qué regula la ley

- **Asesoría de inversión:** dar evaluaciones o recomendaciones a terceros sobre la
  conveniencia de invertir. La CMF considera a los **criptoactivos instrumentos financieros**,
  así que aplica. Requiere inscripción en el Registro de Prestadores de Servicios
  Financieros (RPSF) y autorización de la CMF.
- **Enrutamiento de órdenes:** canalizar órdenes de terceros hacia intermediarios o
  sistemas de transacción.

## Qué queda fuera (según lo revisado)

1. **Contenido informativo o educativo:** objetivo, neutral, basado en hechos verificables,
   sin intención de influir en si conviene invertir.
2. **Órdenes hacia entidades en el extranjero:** la CMF señaló que si se enrutan a entidades
   ubicadas en el extranjero, el servicio no se presta en Chile. Binance está en el
   extranjero.
3. **Análisis sin recomendaciones** (excluido en la norma anterior; verificar en la NCG 502).

## Señales que la CMF considera "asesoría"

- Opiniones subjetivas sobre qué conviene invertir.
- **Difundir resultados de operaciones** o carteras.
- Hacerlo de forma habitual y profesional.

## Reglas de diseño del producto

| ✅ Hacer | ❌ Evitar |
|---|---|
| El usuario **configura** su bot (monedas, riesgo, capital, apalancamiento, kill-switch) | Que el usuario solo "enchufe" nuestra estrategia sin decidir nada |
| Plantillas **neutrales** con explicación objetiva y riesgos | Llamar a una plantilla "la mejor" o "la más rentable" |
| Cuenta y API key **del usuario**, **sin permiso de retiro** | Recibir, custodiar o mover dinero de usuarios |
| **Suscripción fija** | % de ganancias o comisión por operación |
| Contenido **educativo** sobre gestión de riesgo | Señales tipo "compra esto ahora" |
| Cada usuario ve **sus propios** resultados dentro de la app | Publicitar rentabilidades o backtests ("gana X%") |
| Avisos de riesgo claros y aceptados | Prometer o sugerir ganancias |

**Tensión a cuidar:** la promesa "invertir de forma más segura" se acerca a una
recomendación. Comunicar el producto como **herramienta de gestión de riesgo que el usuario
configura**, no como "invertimos mejor por ti".

## Si hubiera que inscribirse

- Se puede como **persona natural** (solo asesoría) o como **SpA**.
- Según las fuentes, **no se exige capital mínimo ni garantía** para asesoría de inversión
  *(confirmar en la versión vigente)*.
- Quien entrega las recomendaciones debe acreditar **formación en mercado financiero**
  (programa de 4–6 semestres según la versión de la norma). Alternativa: un socio o
  asesor con ese título dentro de la SpA.
- Trámite en línea en la CMF; la CMF tiene **hasta 6 meses** para autorizar.
- Obligaciones: perfilamiento de clientes, conflictos de interés, código de conducta,
  información periódica a la CMF.

## Otros puntos legales

- **Términos de Binance:** operar con la key del propio usuario; evaluar el programa oficial
  para plataformas (Binance Link / Broker).
- **Impuestos (SII):** facturar las suscripciones (SpA recomendada).
- **Consumidor (Sernac):** sin publicidad engañosa; términos claros de cobro y baja.
- **Datos personales:** proteger datos y keys de usuarios (ley de protección de datos
  personales).
- **Usuarios de otros países:** quedan bajo sus propias leyes; el MVP apunta solo a Chile.

## Pasos legales

1. Consulta formal a la CMF describiendo el modelo (fase 0).
2. Abogado fintech: revisión del diseño, términos y condiciones, política de privacidad y
   aviso de riesgo.
3. Constituir SpA.
4. Si la CMF lo exige: inscripción en el RPSF (con socio acreditado si hace falta).

## Fuentes

- [CMF — Parámetros para considerar asesoría la difusión en redes sociales](https://www.cmfchile.cl/portal/prensa/625/w4-article-82812.html)
- [CMF — NCG 502 (2024)](https://www.cmfchile.cl/normativa/ncg_502_2024.pdf)
- [CMF — Inscripción en el RPSF y autorización de servicios](https://www.cmfchile.cl/portal/principal/623/w4-article-60920.html)
- [CMF Educa — Asesores de inversión](https://www.cmfchile.cl/educa/621/w3-propertyvalue-48194.html)
- [CMF — Asesorías de inversión en Chile: regulación y tendencias](https://www.cmfchile.cl/portal/prensa/615/articles-100437_doc_pdf.pdf)
- [Ley 21.521 — Biblioteca del Congreso Nacional](https://www.bcn.cl/leychile/Navegar?idNorma=1187323)
- [Zesty — Licencia de enrutamiento de órdenes](https://www.zestyfinance.com/blog/no-toda-regulacion-frena-esta-ordena)
- [Diario Financiero — Guía de la norma de asesores de inversión](https://www.df.cl/asesores-de-inversion-guia-para-entender-la-nueva-norma-que-comenzo-a)
- [ChileAtiende — Registro de asesores de inversión](https://chileatiende.gob.cl/fichas/111140/1/pdf)
