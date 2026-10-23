# openbanking-mcp

Servidor MCP de **finanzas personales de solo lectura** sobre **Open Banking** (PSD2),
vía **[Enable Banking](https://enablebanking.com)**. Consulta cuentas, saldos y
movimientos de tu banco y genera analítica: gasto por categoría, suscripciones,
cargos inusuales y resúmenes mensuales — desde Claude Desktop, Cursor o ChatGPT.
Incluye además un **panel web de consulta**, pensado para el móvil, para analizar
ingresos, gastos y ahorro de forma visual; tirando hacia abajo recarga la página o, con un tirón largo,
lanza una sincronización ([Panel web](#panel-web-dashboard)).

> **Solo lectura.** No inicia pagos ni transferencias: solo se piden permisos de datos
> (cuentas, saldos y movimientos).

> **Funciona con casi toda la banca europea** (CaixaBank incluido). El banco se elige
> con `ENABLEBANKING_ASPSP_NAME` en tu `.env`.

## Aviso

Proyecto independiente, **no afiliado ni respaldado por Enable Banking ni por ningún banco**.
Manejas **tus propios datos bancarios** bajo tu responsabilidad: cada quien autohospeda
con sus credenciales y los datos **nunca salen de tu máquina** (SQLite local; tokens y
clave privada cifrados/protegidos en `data/`, que está en `.gitignore`). Software
entregado "tal cual", sin garantías (ver [LICENSE](LICENSE)). Lee las [notas PSD2](#notas-psd2).

## Arquitectura

```txt
Tu banco (PSD2)
   -> Enable Banking (AIS)          [interfaz BankDataProvider]
   -> Capa de sincronización        (pull idempotente e incremental)
   -> SQLite (SQLAlchemy)
   -> Capa de analítica             (funciones puras)
   -> Servidor MCP (solo lectura)   -> Cliente MCP: Claude Desktop / Cursor / ChatGPT
   -> Panel web (solo lectura)      -> Navegador del móvil o del PC, en la red local
```

Tres superficies sobre la misma base de datos: el **CLI** (`finmcp ...`), el **servidor MCP**
(las mismas operaciones que el CLI, salvo `auth`) y el **panel web** (`finmcp web`), un
servicio aparte en otro puerto.

El servidor MCP **lee de SQLite, nunca llama al banco en caliente**. La sincronización
es un proceso aparte (`finmcp sync`, manual o por cron).

> ¿Por qué Enable Banking? Es el agregador AIS **self-serve y gratis para uso personal**
> que cubre la banca europea. Es el único proveedor soportado por el proyecto; el código
> mantiene una interfaz `BankDataProvider` por si se añade otro proveedor en el futuro.

## Paso a paso

### 1. Instala el proyecto

```bash
git clone https://github.com/guille-near/openbanking-mcp.git && cd openbanking-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Registra tu app en Enable Banking

1. Entra en **[enablebanking.com](https://enablebanking.com) → Control Panel** y regístrate.
2. Crea una **aplicación**:
   - Generación de clave: **"Generate in the browser… export private key"**.
   - **Allowed redirect URLs**: `https://localhost:3000/callback` (exige **HTTPS**).
   - Rellena nombre, email y (si los pide) URLs de privacidad/términos.
3. Al registrar, **descarga la clave privada** (`.pem`) — **solo se muestra una vez** — y
   **copia el Application ID** (es el nombre del fichero `.pem`).
4. **Restricted Production**: en la app, pulsa **Link accounts** y vincula (lista blanca)
   las cuentas de tu banco que quieras leer. En modo *Restricted* solo se pueden acceder
   esas cuentas (no requiere due diligence; perfecto para uso personal).

### 3. Configura el `.env`

```bash
cp .env.example .env
```

Guarda la clave privada en `data/enablebanking_private.pem` y edita el `.env`:

```dotenv
ENABLEBANKING_APP_ID=<tu Application ID>
ENABLEBANKING_COUNTRY=ES
# ENABLEBANKING_KEY_PATH=/ruta/a/clave.pem   # solo si NO usas data/enablebanking_private.pem
```

### 4. Elige tu banco

```bash
finmcp institutions            # lista las entidades (p.ej. "CaixaBank · ES")
```
Fija el nombre **exacto** en el `.env`:
```dotenv
ENABLEBANKING_ASPSP_NAME=CaixaBank
```

### 5. Autoriza y sincroniza

```bash
finmcp auth        # abre tu banco para el SCA
finmcp sync        # baja cuentas/saldos/movimientos a SQLite
finmcp accounts    # comprobación
```

En `finmcp auth`, tras el SCA el navegador irá a `https://localhost:3000/callback` y
mostrará un **error de conexión: es normal**. Copia el valor de `code` de la barra de
direcciones (o pega la URL entera) cuando el CLI lo pida. El código nunca sale de tu máquina.

> El consentimiento dura **~90 días** (límite PSD2); pasado ese plazo, repite `finmcp auth`.

## Varios bancos

Puedes vincular más de un banco (p.ej. CaixaBank y BBVA) a la vez. Define bancos
numerados en el `.env` (ver `.env.example`):

```dotenv
FINMCP_BANK_1_ID=caixa
FINMCP_BANK_1_ASPSP_NAME=CaixaBank
FINMCP_BANK_1_COUNTRY=ES

FINMCP_BANK_2_ID=bbva
FINMCP_BANK_2_ASPSP_NAME=BBVA
FINMCP_BANK_2_COUNTRY=ES
```

Autoriza cada uno por separado y comprueba el estado:

```bash
finmcp auth --bank caixa
finmcp auth --bank bbva
finmcp banks list           # lista bancos configurados y si están vinculados
finmcp sync                 # sincroniza todos; un fallo en uno no bloquea a los demás
```

Si solo defines `ENABLEBANKING_ASPSP_NAME` (sin `FINMCP_BANK_1_*`), el proyecto sigue
funcionando en modo de un solo banco (`--bank` no es necesario).

## Comandos

| Comando | Descripción |
|---|---|
| `finmcp auth [--bank ID]` | Autoriza con un banco y guarda la sesión cifrada |
| `finmcp banks list` | Lista los bancos configurados y su estado de vínculo |
| `finmcp institutions` | Lista las entidades disponibles (para fijar `ENABLEBANKING_ASPSP_NAME`) |
| `finmcp sync` | Trae cuentas/saldos/movimientos a SQLite (por banco) |
| `finmcp accounts` | Lista las cuentas locales (las manuales, marcadas `· manual`) |
| `finmcp add-account --bank B --name N [--iban …] [--type SVGS]` | Da de alta una **cuenta manual**: no se sincroniza; sus movimientos llegan por `import-csv` ([Cuentas manuales](#cuentas-manuales-sin-sincronización)) |
| `finmcp set-balance ID IMPORTE [--at FECHA]` | Fija el saldo de una cuenta manual |
| `finmcp balances` | Saldo más reciente de cada cuenta |
| `finmcp transactions` | Movimientos (`--account`, `--bank`, `--from`, `--to`, `--type`, `--tag`, `--limit`) |
| `finmcp search TEXTO` | Busca movimientos por comercio o concepto (`--tag`) |
| `finmcp spend` | Gasto por categoría (`--from`, `--to`) |
| `finmcp bills` | Recibos / pagos recurrentes marcados vía reglas (`--months`) |
| `finmcp unusual` | Cargos atípicos (`--from`, `--to`) |
| `finmcp summary AÑO MES` | Resumen mensual (con sección aparte de movimientos no computables) |
| `finmcp status` | Estado de la última sincronización por banco |
| `finmcp import-csv` | Importa movimientos desde un CSV (histórico anterior a 90 días) |
| `finmcp categorize` | Reaplica tus reglas de categorización |
| `finmcp rules add/update/remove/list/tag/untag/neutral/bill` | Añade, edita, elimina y lista reglas de categorización, sus etiquetas, sus condiciones de neutral/recibo y su cuenta (`--account`/`--bank`, opcional) |
| `finmcp neutral TX_ID...` | Marca movimientos como no computables (`--off` para desmarcar) |
| `finmcp recategorize TX_ID CATEGORÍA [--is-bill/--no-is-bill]` | Fija a mano la categoría de un movimiento (y opcionalmente su condición de recibo); las reglas no lo volverán a tocar |
| `finmcp skip-rules TX_ID...` | Desactiva las reglas para esos movimientos (`--off` para reactivarlas) |
| `finmcp tags list/add/delete/assign/remove/spend` | Catálogo de etiquetas y etiquetado manual |
| `finmcp serve` | Arranca el servidor MCP (stdio; `--http` para remoto) |
| `finmcp web --host IP [--port 8790]` | Arranca el panel web en la red local: consulta en solo lectura y, tirando hacia abajo, recarga la página o (tirón largo) lanza una sincronización ([Panel web](#panel-web-dashboard)) |
| `finmcp web-pin [--remove]` | Fija (o quita) el PIN del panel web; guarda solo su huella en `.env` |

## Herramientas MCP

Equivalentes a los comandos del CLI, salvo `auth` (solo CLI, requiere navegador) y `serve`:

`list_accounts` · `get_balances` · `get_transactions` · `search_transactions` ·
`spend_by_category_tool` · `list_bills` · `unusual_charges` ·
`monthly_summary_tool` · `sync_status` · `sync` · `list_banks` · `list_institutions` ·
`import_csv` · `add_manual_account` · `set_manual_balance` · `categorize` · `add_category_rule` · `update_category_rule` · `remove_category_rules` · `list_category_rules` ·
`add_rule_tags` · `remove_rule_tags` · `set_rule_neutral` · `set_rule_is_bill` · `list_tags` · `create_tag` ·
`delete_tag` · `tag_transactions` · `untag_transactions` · `set_transactions_neutral` ·
`recategorize_transaction` · `set_transactions_skip_rules` · `spend_by_tag_tool`

> `import_csv` lee rutas del disco del servidor y `add_category_rule`/`update_category_rule`/`remove_category_rules`/`categorize`
> escriben en SQLite: en modo `--http` define siempre `FINMCP_HTTP_TOKEN`.

> Cargos inusuales usa **mediana + MAD** (robusto): un único pico no contamina
> su propia línea base, así que se detecta de verdad.

## Conectar a Claude Desktop

En `claude_desktop_config.json` (usa la **ruta absoluta** a tu clon del repo):

```json
{
  "mcpServers": {
    "openbanking": {
      "command": "/RUTA/ABSOLUTA/A/openbanking-mcp/.venv/bin/finmcp",
      "args": ["serve"]
    }
  }
}
```

El servidor lee de SQLite; recuerda correr `finmcp sync` (manual o por cron) para
mantener los datos al día.

## Conectar a ChatGPT

ChatGPT solo se conecta a servidores MCP **remotos por HTTP(S)**. Hay que exponer el
servidor por una URL pública y añadirlo como *connector* en Modo Desarrollador.

> ⚠️ **Datos bancarios por una URL pública.** Usa SIEMPRE bearer token y HTTPS.
> Para uso solo-local, Claude Desktop (stdio) es más seguro.

```bash
export FINMCP_HTTP_TOKEN="<token-largo-aleatorio>"
finmcp serve --http --port 8000        # expone POST /mcp (401 sin el token)
ngrok http 8000                         # túnel HTTPS -> https://xxxx.ngrok.app
```

En ChatGPT → Connectors → *Add custom connector*: URL `https://xxxx.ngrok.app/mcp`,
cabecera `Authorization: Bearer <FINMCP_HTTP_TOKEN>`.

## Panel web (dashboard)

Tercera superficie de finmcp, junto al CLI y al servidor MCP: un panel web **de consulta**,
pensado primero para el móvil, para analizar ingresos, gastos y ahorro por categoría y periodo,
con detalle hasta cada movimiento. No modifica tus datos: lo único que puede hacer es pedir una
sincronización con los bancos (ver «Tirar para recargar o sincronizar»).

```bash
finmcp web-pin                                     # fija el PIN (de 4 a 12 cifras); se pide dos veces
finmcp web --host 192.168.1.10                     # IP del equipo en tu red local; puerto 8790
finmcp web --host 192.168.1.10 --host 100.x.y.z    # y además en su IP de Tailscale
```

Abre `http://<IP>:8790/` en el navegador del móvil o del PC e introduce el PIN.

- **Diario** (la pestaña de apertura): el día a día, sin agregaciones.
  - **Saldos:** el último saldo conocido de cada cuenta, con la hora de la foto y un semáforo de frescura (ámbar si falló el último sync, rojo si la foto tiene más de 36 h).
    - **Total:** la suma de los últimos saldos de todas las cuentas, en cifra grande y alineada a la izquierda, sin rótulo (la calcula el servidor, en céntimos; solo aparece con dos o más cuentas). Debajo, en letra pequeña y con un semáforo delante, de cuántas cuentas es y la hora del saldo más antiguo, y avisa de las salvedades: una cuenta sin saldo no suma, una con el saldo desactualizado sí (con aviso) y las que solo dan «saldo disponible» (no contable) se nombran. El semáforo es el peor de todas las cuentas. Si los saldos están en monedas distintas no hay total.
    - **Un solo bloque, compacto y sin líneas:** los saldos son una única tarjeta: el total arriba y debajo las cuentas en una rejilla de dos columnas, sin ninguna línea interior (solo el borde exterior; las celdas se distinguen por el espacio y la cuenta elegida por su fondo y un aro). Cada cuenta ocupa tres líneas (banco, cifra y, pequeña, terminación del IBAN y hora del saldo). Con todo en orden la sección mide unos 230 px en un móvil de 412 px de ancho (antes, 388 px con tarjetas sueltas). Si una cuenta tiene un aviso (sync fallida o saldo viejo), su celda crece lo que haga falta para el texto.
    - **Animación:** al cargar la página, las cifras de esta sección ruedan como un cuentakilómetros hasta su valor, una sola vez por carga (filtrar, elegir una cuenta o volver del detalle no la repiten). Con «reducir movimiento» activado en el sistema, no hay animación.
  - **Movimientos:** la lista plana de todos, también los no computables, del más reciente al más antiguo, por tramos.
  - **Filtros:** cuenta, cargos o abonos, una marca (sin categoría, recibos, no computables, manuales), etiqueta, fechas y un buscador sin acentos que también mira el importe.
  - **Saldo de cada día:** con una cuenta elegida, cada día muestra su saldo al cierre, calculado hacia atrás desde la última foto del banco y contrastado con todas las fotos reales.
  - **Detalle:** al tocar un movimiento se ven sus datos originales del banco (contraparte, concepto íntegro, fechas, referencia y el JSON completo) y un botón «Copiar id».
- **Mes**: ingresos y gastos del mes por categoría (donut y lista con minigráfica de 6 meses),
  con detalle por subcategoría y movimientos. Se cambia de mes con las flechas, el selector o deslizando.
  Toda lista de movimientos del panel (también las de Evolución y Tendencias) se abre con la fecha
  descendente, lo más reciente primero; los botones de orden son «Fecha ↓», «Fecha ↑» e «Importe».
- **Evolución**: *Mes a mes* (gasto del mes en curso frente al mes anterior y a las medias de 3 y
  6 meses, a la misma fecha, y el ahorro de cada mes), *Acumulado* (3 meses, 6 meses, 1 año, año
  actual o todo; solo meses cerrados) y *Periodo contra periodo* (mes, trimestre, semestre o año).
- **Tendencias**: 12 meses de las categorías de gasto que elijas, con su tendencia.

**Modo oscuro.** Automático, según el tema claro/oscuro del sistema operativo o del navegador
(`prefers-color-scheme`); no hace falta tocar nada. Un botón ☀/☾ en la cabecera de **Diario**,
**Evolución** y **Tendencias** (no en Mes ni en el detalle de un movimiento, que no llevan icono de
cabecera propio: se alterna cambiando a una de esas tres pestañas) permite forzarlo con independencia
del sistema, en el ciclo automático → oscuro → claro → automático; la elección se recuerda en el
dispositivo (`localStorage`), nunca en el servidor.

Criterios de cálculo (salvo en Diario, que no agrega): los movimientos neutrales no computan; son categorías de ingreso las que
empiezan por «Ingresos», más «Otros ingresos» e «Intereses»; las devoluciones se restan del gasto
de su categoría, y el ahorro coincide con el neto de `finmcp summary`. Toda comparación con el mes
en curso se hace **a la misma fecha** (del día 1 al día de hoy de cada mes).

**Tirar para recargar o sincronizar.** En el móvil, tirando hacia abajo desde arriba del todo de la pestaña
**Diario** (la lista, con o sin filtros), lo que ocurre al soltar depende de lo largo que sea el tirón; el
indicador de arriba lo va diciendo mientras se tira:

- **Tirón corto** (menos de unos 200 px): no hace nada.
- **Tirón mediano** (unos 200–370 px): **recarga la página** (la recarga nativa del navegador, que además
  revalida el JavaScript y el CSS y conserva la ruta y los filtros). No habla con los bancos.
- **Tirón largo** (más de unos 370 px): lanza una **sincronización** con los bancos y, cuando termina,
  recarga la lista con los datos nuevos.

Se decide por dónde está el dedo al soltar: se puede pasar de largo y volver atrás para elegir el mediano.
La sincronización solo se dispara en Diario: en Mes, Evolución, Tendencias, las listas de movimientos de un
periodo y el detalle de un movimiento el panel no captura el gesto y el navegador conserva el suyo. Un aviso
resume el resultado de la sincronización («Sincronizado · 3 movimientos nuevos», o qué banco falló y por qué).

- **Cómo:** `POST /api/sync` arranca `finmcp sync --from <hoy − 30 días>` (el mismo rango que el
  cron diario) en un **proceso aparte**; el panel solo lo vigila y sigue abriendo la BD en solo
  lectura. Mientras tanto, la interfaz consulta `GET /api/sync`.
- **Una a la vez:** `finmcp sync` toma un bloqueo de fichero (`data/sync.lock`), también desde el
  CLI, el servidor MCP y el cron. Si ya hay otra sincronización en curso, sale con código 75 y el
  panel lo dice y muestra los datos actuales.
- **Freno:** no se lanza otra si la última sincronización, **de cualquier origen**, terminó hace menos de
  `FINMCP_WEB_SYNC_COOLDOWN` segundos (300 por defecto; `0` lo quita). PSD2 limita los accesos de cada
  banco (el 429 es habitual) y un tirón repetido podría agotar el cupo del sync diario. Entonces el
  panel solo recarga los datos y avisa de cuándo podrá volver a sincronizar.
- **Límite:** una sincronización se cancela a los 300 s.
- Exige el mismo PIN y la misma restricción de red que el resto de la API. Si la sesión caducó, el gesto lleva a
  la pantalla del PIN. Sin la sincronización disponible (`meta.sync` falso) el panel tampoco captura el gesto en Diario.

Seguridad:

- Servicio aparte del servidor MCP, en otro puerto (8790; el MCP `--http` usa el 8000).
- Abre la base de datos en **solo lectura** (SQLite `mode=ro`); los datos solo se consultan con
  `GET`. Los únicos `POST` son entrar y salir con el PIN y `POST /api/sync` (solo JSON, tras el PIN),
  que no escribe él mismo: lanza `finmcp sync` en otro proceso.
- **Solo red local**: atiende clientes de la red local (loopback y rangos privados) y de Tailscale
  si escucha en su IP; al resto responde 403. Se niega a escuchar en `0.0.0.0` o en una IP pública.
  **No lo publiques en Internet** (ni con Tailscale Funnel ni redirigiendo puertos).
- **PIN** (`finmcp web-pin`): `.env` guarda solo su huella (`FINMCP_WEB_PIN_HASH`, PBKDF2-SHA256
  con sal). Al acertar, el navegador recibe una cookie de sesión firmada (`HttpOnly`,
  `SameSite=Strict`) válida 30 días; cambiar el PIN cierra todas las sesiones. Tras 5 fallos
  seguidos, cada nuevo fallo bloquea el acceso un tiempo creciente (30 s, 1 min, 2 min... hasta
  1 h); reiniciar el servicio desbloquea. Sin PIN, el panel queda abierto a la red local (avisa al
  arrancar). Por HTTP en la red local el PIN viaja sin cifrar por la Wi-Fi de casa; por Tailscale
  va cifrado.
- CSP estricta y sin recursos externos; la API no se guarda en caché y el navegador solo recuerda
  preferencias, nunca cifras.

Como servicio de usuario de systemd (Linux), con la plantilla `deploy/finmcp-web.service`:

```bash
sed -e "s|__PROJECT_DIR__|$PWD|g" -e "s|__LAN_IP__|192.168.1.10|g" \
  deploy/finmcp-web.service > ~/.config/systemd/user/finmcp-web.service
systemctl --user daemon-reload && systemctl --user enable --now finmcp-web
loginctl enable-linger "$USER"     # para que arranque sin sesión abierta
journalctl --user -u finmcp-web -f # registro
```

## Categorías personalizadas

Enable Banking no envía categoría en los movimientos, así que las defines tú con reglas:

```bash
finmcp rules add "mercadona" "Supermercado"
finmcp rules add "vodafone" "Telefonía" --field merchant
finmcp rules list
finmcp categorize            # reaplica todas las reglas
```

Las reglas se reaplican automáticamente al final de cada `finmcp sync`.
`my_category` (manual/regla) tiene prioridad sobre cualquier categoría del proveedor.

Para **corregir o retirar reglas existentes** sin crear duplicados, `rules update` edita
solo los campos indicados y `rules remove` elimina (una o varias); ambas reaplican las
reglas al momento:

```bash
finmcp rules update 42 --category "Nueva categoría"       # renombra la categoría
finmcp rules update 42 --pattern "otro texto"             # corrige el patrón
finmcp rules update 42 --amount-min 100 --amount-max 150  # ajusta el rango de importe
finmcp rules update 42 --clear-amount-min                 # quita el mínimo
finmcp rules update 42 --priority 50                      # prioridad (menor = se evalúa antes)
finmcp rules update 42 --neutral                          # marca neutral (--no-neutral para desmarcar)
finmcp rules remove 42                                    # elimina la regla (varias: rules remove 42 43)
```

### Reglas por cuenta (opcional)

Una regla puede restringirse a una única cuenta — útil cuando el mismo texto (p. ej.
"Transferencia emitida periódica") significa cosas distintas según el banco. Por
defecto (sin `--account`/`--bank`) una regla sigue aplicando a todas las cuentas,
igual que antes de esta opción.

```bash
finmcp accounts                                           # lista cuentas con su id
finmcp rules add "cuota club" "Ocio" --account 2131bb2d-...   # por id exacto
finmcp rules add "cuota club" "Ocio" --bank ING               # por banco (si tiene 1 sola cuenta)
finmcp rules update 42 --account 2131bb2d-...             # restringe una regla existente
finmcp rules update 42 --clear-account                    # vuelve a aplicar a todas las cuentas
```

`--bank` es una comodidad sobre `--account`: solo resuelve si ese banco tiene
exactamente una cuenta local; si tiene varias (p. ej. tras una reautenticación que
deja un id antiguo vivo), pide usar `--account` con el id exacto. `rules list`
muestra la cuenta como `[cuenta=BANCO·…últimos4]` cuando la regla está restringida.

## Movimientos no computables (neutrales)

Traspasos entre cuentas propias, liquidaciones de tarjeta cuyas compras ya están
sincronizadas, aportaciones a ahorro… no son ingreso ni gasto. Márcalos como
**neutrales** y quedarán fuera de ingresos/gastos, gasto por categoría/etiqueta,
suscripciones y cargos atípicos. `summary` los lista en una sección aparte.

```bash
finmcp rules add "traspaso" "Traspasos internos" --neutral
finmcp rules neutral RULE_ID         # marca una regla existente (--off para desmarcar)
finmcp neutral TX_ID1 TX_ID2         # marca movimientos sueltos
```

Los movimientos heredan `neutral` de la regla ganadora; si una regla casa, al
reaplicar reglas su valor prevalece sobre el marcado manual.

## Recategorización manual (override de reglas)

Para corregir la categoría de un movimiento concreto sin crear una regla:

```bash
finmcp recategorize TX_ID "Categoría correcta"
```

Fija `my_category` y activa `skip_category_rules`: mientras esté activo, reaplicar
las reglas (sync, `categorize`, cambios de reglas…) **no tocará** ese movimiento
—ni su categoría, ni su `neutral`, ni sus etiquetas de reglas—. En `transactions`
y `search` aparece con ` (manual)`.

```bash
finmcp skip-rules TX_ID1 TX_ID2     # activa el flag sin tocar la categoría
finmcp skip-rules TX_ID --off       # devuelve el movimiento a las reglas
```

Con `--off` el movimiento vuelve a quedar sujeto a las reglas: en el siguiente
`categorize` (o sync) recupera la categoría que le asigne la regla ganadora.

## Etiquetas

Un movimiento puede tener varias etiquetas (catálogo canónico en `tags`, relación N:M).
Las reglas de categoría pueden llevar etiquetas: al aplicarse, cada movimiento recibe
la unión de las etiquetas de **todas** las reglas que casan (la categoría sigue siendo
la de la regla ganadora).

```bash
finmcp rules add "comercializadora baser" "Suministros - Electricidad" --tag Tomiño
finmcp rules list                # muestra el id de cada regla: ... (id=3)
finmcp rules tag RULE_ID casa    # añade etiquetas a una regla existente
finmcp rules untag RULE_ID casa  # y las quita (se reaplican las reglas)
finmcp tags assign viaje TX_ID1 TX_ID2   # etiquetado manual
finmcp tags remove tomiño TX_ID          # las reglas no la volverán a poner
finmcp transactions --tag tomiño
finmcp tags spend --from 2026-01-01
```

Los nombres se normalizan (minúsculas, sin espacios extremos) y se crean al usarlos.
`finmcp tags delete` falla si la etiqueta está en uso por movimientos o reglas.

## Importar histórico antiguo (CSV)

Las APIs PSD2 solo dan ~90 días de histórico. Para movimientos más antiguos,
exporta tus movimientos desde la web de tu banco (**Excel `.xlsx` o CSV/TXT**) e impórtalos:

```bash
finmcp import-csv movimientos.xlsx --iban ES58...   # o un .csv / .txt
```

Acepta **Excel (`.xlsx` y `.xls`) y texto (CSV/TXT)**; detecta el delimitador, las
cabeceras y el formato español de fecha/importe. Soporta importe en una columna con
signo o en columnas **Ingreso/Gasto** separadas (formato CaixaBank), y si el fichero
trae varias cuentas (columna *Número de cuenta*) **mapea cada movimiento a su cuenta**.
**Deduplica** por (cuenta, día, importe, tipo), así que es seguro reimportar o
solapar con lo que ya bajó la API. Las reglas de categorización se aplican solas.

## Cuentas manuales (sin sincronización)

Una cuenta que el banco no expone por PSD2 (p. ej. una cuenta de ahorro que no entra en el
consentimiento) se puede llevar igualmente en finmcp como **cuenta manual**:

```bash
finmcp add-account --bank ING --name "Cuenta Naranja" --iban "ES91 2100 0418 4502 0005 1332" --type SVGS
finmcp set-balance <id> 1234.56                  # saldo de ahora; --at 2026-10-23 = al cierre de ese día
finmcp import-csv movimientos.xlsx --iban ES9121000418450200051332
```

- El IBAN se guarda sin espacios y se comprueban sus dígitos de control; no puede haber dos cuentas con el mismo.
- El `--bank` es solo la etiqueta del banco: el `sync` nunca toca estas cuentas.
- `set-balance` solo vale para cuentas manuales (el saldo de las sincronizadas lo da el banco).
- En el panel, la cuenta sale en los saldos del Diario con «manual · <fecha del saldo>», sin semáforo de
  sincronización (no se marca desactualizada por antigüedad: la fecha ya dice de cuándo es) y suma en el total.

## Sincronización programada (macOS / launchd)

```bash
# Sustituye __PROJECT_DIR__ por la ruta absoluta de tu clon
sed -i '' "s|__PROJECT_DIR__|$PWD|g" deploy/com.openbanking-mcp.sync.plist

# Copia el LaunchAgent y actívalo (sync cada 6 h)
cp deploy/com.openbanking-mcp.sync.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.openbanking-mcp.sync.plist

# Logs en data/sync.log · para parar:
launchctl unload ~/Library/LaunchAgents/com.openbanking-mcp.sync.plist
```

## Notas PSD2

- El consentimiento PSD2 caduca: hay que **re-autorizar con SCA cada ~90 días** (`finmcp auth`).
- En **Restricted Production** solo se leen las cuentas que hayas vinculado (lista blanca)
  en el panel de Enable Banking.
- El histórico disponible suele limitarse a ~90 días por las APIs PSD2 de los bancos.

## Desarrollo

```bash
pip install -e ".[dev]"
pytest                       # suite de tests (analítica, mapper, sync, config, panel web)
node --test tests/js/lib.test.js   # funciones puras de la interfaz del panel (Node ≥ 18)
```

La analítica son **funciones puras** testeadas contra una SQLite en memoria; el flujo de
`sync` se prueba con un cliente falso (sin tocar el banco). CI en GitHub Actions corre la
suite en Python 3.11–3.13 (`.github/workflows/ci.yml`).

## Licencia

[MIT](LICENSE) © 2026 Guille Pérez
