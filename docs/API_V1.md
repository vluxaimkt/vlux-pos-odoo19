# API VLUX v1

Contrato estable entre las interfaces VLUX y Odoo. Es la base de la
[Iniciativa 2](PLAN_INICIATIVAS.md) y del camino descrito en
[ARQUITECTURA_HEADLESS.md](ARQUITECTURA_HEADLESS.md): las pantallas llaman, el
servidor decide.

Raíz: `https://<tienda>/vlux/api/v1`

## 1. Regla de compatibilidad

`v1` **nunca** cambia de forma que rompa a un cliente ya instalado en una
tienda. Se pueden añadir campos y endpoints; quitar o cambiar el significado de
algo existente obliga a `v2`, y ambas versiones conviven mientras haya cajas en
campo. Cada respuesta lleva la cabecera `X-Vlux-Api-Version`.

## 2. Forma de las respuestas

```json
{"ok": true,  "data": { ... },                          "request_id": "8f3c..."}
{"ok": false, "error": "FORBIDDEN_SCOPE", "message": "...", "request_id": "8f3c..."}
```

`request_id` viaja también en la cabecera `X-Request-Id` y es lo que se cita al
reportar un problema: aparece en el registro del servidor junto al error real.
Todas las respuestas son `no-store`: nunca se cachean en el dispositivo ni en
Cloudflare.

Códigos de error del contrato:

| Código | HTTP | Significado |
| --- | --- | --- |
| `MISSING_TOKEN` | 401 | Falta `Authorization: Bearer <token>` |
| `INVALID_TOKEN` | 401 | Token desconocido, revocado, expirado o de un usuario desactivado |
| `FORBIDDEN_SCOPE` | 403 | El token no tiene el alcance que el endpoint exige |
| `RATE_LIMITED` | 429 | Más de 600 solicitudes por minuto con el mismo token |
| `INTERNAL_ERROR` | 500 | Fallo del servidor; el detalle queda en el registro, nunca en la respuesta |

## 3. Autenticación

Cada caja o interfaz tiene **su propio token**, revocable por separado:

```
Authorization: Bearer <token>
```

Garantías, heredadas del emparejamiento del escáner que ya opera en staging:

- Se guarda **solo el hash** (SHA-256). Una copia de la base de datos no sirve
  para entrar.
- El texto plano se muestra **una vez**, al emitirlo. Si se pierde, se revoca y
  se emite otro.
- Un token **nunca puede hacer más que el usuario detrás de él**: la petición se
  ejecuta *como* ese usuario, así que las reglas de registro y los permisos de
  Odoo se aplican igual que en la pantalla; el alcance solo estrecha lo que ese
  usuario ya podía hacer. Los alcances, además, se limitan a su rol VLUX.
- Expiración opcional, revocación inmediata y `last_used_at` para detectar
  dispositivos olvidados.

### Alcances

| Alcance | Permite |
| --- | --- |
| `system:read` | Estado del sistema |
| `catalog:read` | Leer catálogo y precios |
| `catalog:write` | Alta y edición de productos |
| `orders:write` | Registrar ventas |
| `session:manage` | Abrir y cerrar caja |
| `dashboard:read` | Indicadores del negocio |

Qué puede otorgar cada rol (el resto se rechaza al emitir, no al usar):

| Rol VLUX | Alcances que puede otorgar |
| --- | --- |
| Owner, Administrador | todos |
| Supervisor | todos menos `catalog:write` |
| Cajero | `system:read`, `catalog:read`, `orders:write` |
| Operador de inventario | `system:read`, `catalog:read`, `catalog:write` |
| Auditor | `system:read`, `dashboard:read` |
| Soporte | `system:read` |

### Emitir un token

En Odoo: **Ajustes → API VLUX → Emitir token**. Se elige nombre, usuario,
alcances y expiración; el token aparece una sola vez para copiarlo a la caja.
**Ajustes → API VLUX → Tokens de la API** lista los vigentes y los revocados, y
desde el formulario se revoca.

Desde el shell, para automatizar el aprovisionamiento:

```python
token, raw = env["vlux.api.token"].issue("Caja 1", "catalog:read orders:write", user=env.ref("base.user_admin"))
print(raw)   # única vez
```

## 4. Endpoints

| Método | Ruta | Alcance | Para qué |
| --- | --- | --- | --- |
| GET | `/me` | `system:read` | Identidad del token, sus alcances y la tienda contra la que opera |
| GET | `/openapi.json` | público | El contrato completo, para generar un cliente |

`/me` es también la prueba de vida que debe ejecutar una caja al arrancar:
confirma token, tienda, moneda y hora del servidor.

```bash
curl -s https://tienda.vlux.com.mx/vlux/api/v1/me -H "Authorization: Bearer $VLUX_TOKEN"
```

Las fases B, C y D añaden catálogo con sincronización incremental, venta,
apertura y cierre de caja, y métricas (ver [PLAN_INICIATIVAS.md](PLAN_INICIATIVAS.md)).

## 5. Límites de uso

600 solicitudes por minuto y por token, con una ventana que se reinicia en una
sola sentencia SQL: dos procesos que atienden a la misma caja no pueden dejar
pasar una ventana doble. Es holgado para operar una caja y estrecho para que un
token filtrado sirva para vaciar el catálogo.

El contador vive en una transacción corta propia (`READ COMMITTED`), separada
de la de la petición. Sin eso, Odoo abre cada petición en `REPEATABLE READ` y
dos incrementos simultáneos del mismo contador terminan en
`could not serialize access due to concurrent update`: en una ráfaga de 620
peticiones con 20 en vuelo, 317 respondían 500 y el límite nunca se activaba.
Con la transacción propia, las 620 responden 200 o 429 y el límite se cumple
exacto (`tools/perf/bench_api_burst.py`). Dos consecuencias del diseño:

- **Una petición fallida también cuenta.** El contador se confirma aunque la
  petición termine en error, así que un cliente no puede gastar presupuesto
  gratis provocando errores.
- El registro de último uso del token (`last_used_at`) se actualiza igual, en
  su propia transacción y a lo sumo una vez por minuto, para que una ráfaga
  inicial sobre un token recién emitido no choque consigo misma.

Detrás de Cloudflare, el *Browser Integrity Check* rechaza con 403 (código
1010) algunos `User-Agent` genéricos, por ejemplo `Python-urllib`. Un cliente
de la API debe enviar un `User-Agent` propio (`vlux-pos-client/1.0`); `curl` y
los navegadores pasan sin más.

## 6. Qué se prueba en CI

`vlux_core/tests/test_api_v1.py` falla si el contrato cambia sin querer:
almacenamiento hasheado, alcances limitados por rol, token expirado, revocado o
de usuario desactivado, forma de `/me` y sus cabeceras, token nunca devuelto en
la respuesta, autenticación ausente o inválida, alcance insuficiente, límite de
uso, fuga de detalles en un error interno, y el propio OpenAPI.
