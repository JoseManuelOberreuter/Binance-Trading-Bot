# 05 — Seguridad

La regla más importante: **aunque nos hackeen, nadie puede sacar dinero de las cuentas de
los usuarios.** Todo lo demás se diseña alrededor de eso.

## API keys de Binance

1. **Sin permiso de retiro, obligatorio.** El motor consulta los permisos de cada key
   (`get_account_api_permissions`) y **rechaza** cualquier key con retiros o transferencias
   habilitados. Se vuelve a verificar periódicamente.
2. **IP restringida a nuestro motor.** El onboarding guía al usuario a restringir la key a
   la IP fija de la VM. Una key robada no sirve desde otra IP.
3. **Permisos mínimos:** lectura + Futures. Sin Spot ni Margin (el bot no los necesita y así
   el Spot del usuario queda protegido).
4. **Cifrado asimétrico de punta a punta:**
   - El navegador cifra la key con la **clave pública** del motor antes de enviarla.
   - Supabase guarda solo el texto cifrado.
   - Solo el motor (VM Oracle) tiene la **clave privada** para descifrar, en memoria, al
     momento de usarla.
   - Resultado: una filtración de Supabase o de Vercel **no expone** keys utilizables.
5. **Nunca** se registran keys ni secretos en logs, eventos, errores o métricas.
6. El usuario puede **revocar** la key desde la app (se borra) y desde Binance.

## Cuentas de usuario

- Supabase Auth con verificación de email.
- **2FA obligatorio** para conectar una key de dinero real o cambiar el riesgo/capital.
- Re-autenticación para acciones sensibles (cambiar o borrar la key, cerrar posiciones).
- Bloqueo por intentos fallidos y alertas de inicio de sesión nuevo.

## Base de datos

- **RLS en todas las tablas** con datos de usuario.
- La clave `service_role` de Supabase **solo** vive en la VM del motor (nunca en el front ni
  en el repo).
- `audit_log` de solo inserción.
- Respaldos automáticos diarios (Supabase) y prueba periódica de restauración.

## Motor de bots

- **Stops siempre en Binance:** si el motor se cae, las posiciones siguen protegidas.
- Recuperación de órdenes con respuesta perdida, verificación de margen aislado, errores
  aislados por usuario, reloj sincronizado (ya implementado en `xrp_futures/`).
- **Kill-switch por usuario** (caída máxima) y **kill-switch global** (pausar todas las
  entradas nuevas de todos los usuarios ante un incidente).
- Lock por usuario: un bot nunca corre dos veces a la vez.
- Límites por plan para no exceder la API de Binance (un usuario no puede afectar a otros).

## Infraestructura

- VM con firewall: solo SSH (con llave) y salida a internet; el panel no se expone.
- Secretos en variables de entorno de la VM / Vercel, nunca en Git.
- Dependencias actualizadas y revisión de seguridad antes de cada lanzamiento.

## Plan ante incidentes

1. **Detectar:** alertas por posiciones sin stop, ciclos fallidos, errores de autenticación
   masivos, uso anómalo de la API.
2. **Contener:** kill-switch global (pausa entradas nuevas; stops se mantienen).
3. **Si se sospecha filtración de keys:** invalidar todas las keys almacenadas, pedir a los
   usuarios que las revoquen en Binance y creen nuevas.
4. **Comunicar** a los usuarios afectados y, si corresponde, a la autoridad.
5. **Revisar** la causa y documentarla.

## Checklist antes del lanzamiento (fase 5)

- [ ] Keys con retiro rechazadas automáticamente
- [ ] Cifrado asimétrico verificado (Supabase no puede descifrar)
- [ ] RLS probado: un usuario no ve datos de otro
- [ ] 2FA obligatorio para dinero real
- [ ] Sin secretos en logs ni en el repo
- [ ] Respaldos y restauración probados
- [ ] Kill-switch global probado
- [ ] Alertas de "posición sin stop" funcionando
